import json

from openai import OpenAI

from app.core.config import OPENAI_API_KEY, OPENAI_MODEL, logger
from app.services.prompts import get_system_prompt, get_tools
from app.db.database import search_properties, search_upcoming_properties, price_range

client = OpenAI(api_key=OPENAI_API_KEY)

def detect_type(title: str, description: str) -> str:
    title_lower = title.lower() if title else ""
    desc_lower = description.lower() if description else ""
    
    # 2 bedrooms
    if "غرفتين" in title_lower or "2 غرف" in title_lower or "غرفتين" in desc_lower or "2 غرفه" in title_lower:
        return "two_bedroom"
        
    # 1 bedroom + living room (غرفة وصالة)
    if any(x in title_lower for x in ["وصاله", "وصالة"]):
        return "one_bedroom"
        
    # studio / small room
    return "studio"

def filter_properties(properties: list[dict], apartment_type: str | None) -> list[dict]:
    if not apartment_type:
        return properties
    return [p for p in properties if detect_type(p.get("title"), p.get("description")) == apartment_type]

# Process-memory sessions: phone → list of chat messages
SESSIONS: dict[str, list[dict]] = {}

# Separate store for search result state (not mixed into message history)
# phone → {"results": [...], "index": int}
SEARCH_STATE: dict[str, dict] = {}


def get_session(phone: str) -> list[dict]:
    if phone not in SESSIONS:
        SESSIONS[phone] = [{"role": "system", "content": get_system_prompt()}]
    else:
        for msg in SESSIONS[phone]:
            if msg["role"] == "system":
                msg["content"] = get_system_prompt()
                break
    return SESSIONS[phone]


def process_message(phone: str, message: str) -> tuple[str, list[str]]:
    messages = get_session(phone)
    messages.append({"role": "user", "content": message})

    # ── Phone-scoped tool implementations ──────────────────────────────────────

    def _search(neighborhood: str, max_budget: int, min_budget: int = 0, apartment_type: str | None = None) -> dict:
        """Run DB search, store all results, return only the FIRST property.

        *neighborhood* may be a single name or comma-separated names (for
        direction-based searches like "شمال").
        """
        # Split comma-separated neighborhoods into a list
        if "," in str(neighborhood):
            neighborhoods = [n.strip() for n in neighborhood.split(",") if n.strip()]
        else:
            neighborhoods = neighborhood

        # Map apartment_type to rooms_count filter in SQL (first-level filtering)
        sql_rooms = None
        if apartment_type == "two_bedroom":
            sql_rooms = 2
        elif apartment_type in ("one_bedroom", "studio"):
            sql_rooms = 1

        # Fetch all results matching neighborhood and rooms_count
        # Use high budget to calculate the accurate price range over all stock
        all_results = search_properties(neighborhoods, max_budget=999999, min_budget=0, rooms_count=sql_rooms)

        # Filter by specific type (studio vs one_bedroom) in Python
        all_results = filter_properties(all_results, apartment_type)

        # Separate stock (regardless of budget) and active results (within budget)
        all_stock = all_results
        active_results = [r for r in all_stock if min_budget <= r["monthly_price"] <= max_budget]

        SEARCH_STATE[phone] = {"results": active_results, "index": 0}

        # Calculate min/max prices for the exact type
        if all_stock:
            prices = [r["monthly_price"] for r in all_stock]
            price_min = min(prices)
            price_max = max(prices)
        else:
            price_min, price_max = None, None

        if not active_results:
            # Check if stock exists above budget
            if all_stock:
                return {
                    "found": 0,
                    "exists_above_budget": True,
                    "price_min": price_min,
                    "price_max": price_max,
                }
            return {"found": 0}

        first = active_results[0]
        return {
            "total_found": len(active_results),
            "current_index": 1,
            "has_more": len(active_results) > 1,
            "price_min": price_min,
            "price_max": price_max,
            "property": first,
        }

    def _search_upcoming(
        neighborhood: str,
        max_budget: int,
        min_budget: int = 0,
        apartment_type: str | None = None,
        days_ahead: int = 30,
    ) -> dict:
        """Find apartments becoming available soon."""
        if "," in str(neighborhood):
            neighborhoods = [n.strip() for n in neighborhood.split(",") if n.strip()]
        else:
            neighborhoods = neighborhood

        sql_rooms = None
        if apartment_type == "two_bedroom":
            sql_rooms = 2
        elif apartment_type in ("one_bedroom", "studio"):
            sql_rooms = 1

        results = search_upcoming_properties(
            neighborhoods, max_budget=999999, min_budget=0, rooms_count=sql_rooms, days_ahead=days_ahead
        )
        
        # Filter by type in Python
        results = filter_properties(results, apartment_type)
        
        # Filter by budget
        results = [r for r in results if min_budget <= r["monthly_price"] <= max_budget]

        if not results:
            return {"found": 0}

        return {
            "total_found": len(results),
            "properties": results,
        }

    def _next_property() -> dict:
        """Advance to the next search result and return it."""
        state = SEARCH_STATE.get(phone)
        if not state or not state.get("results"):
            return {"error": "ما في نتائج بحث سابقة. اطلب من العميل يحدد الحي والميزانية مجددًا."}

        results = state["results"]
        new_idx = state["index"] + 1

        if new_idx >= len(results):
            return {"found": 0, "message": "هذه كانت آخر شقة في نتائج البحث."}

        state["index"] = new_idx
        prop = results[new_idx]
        return {
            "total_found": len(results),
            "current_index": new_idx + 1,
            "has_more": new_idx + 1 < len(results),
            "property": prop,
        }

    available_functions = {
        "search_properties": _search,
        "search_upcoming_properties": _search_upcoming,
        "next_property": _next_property,
    }

    # ── Main loop ───────────────────────────────────────────────────────────────

    def _merge_search_calls(tool_calls: list) -> list:
        """If the model issues multiple search_properties calls in one round,
        merge them into a single call with all neighborhoods joined.

        This prevents the LLM from making 3 separate calls (العارض, العقيق, النرجس)
        when it should make one call with all three names.
        """
        search_calls = [
            tc for tc in tool_calls if tc.function.name == "search_properties"
        ]
        other_calls = [
            tc for tc in tool_calls if tc.function.name != "search_properties"
        ]

        if len(search_calls) <= 1:
            return tool_calls  # nothing to merge

        # Parse all search args
        parsed = []
        for tc in search_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            parsed.append(args)

        # Collect all neighborhood names
        all_neighborhoods = []
        for args in parsed:
            nb = args.get("neighborhood", "")
            for n in str(nb).split(","):
                n = n.strip()
                if n and n not in all_neighborhoods:
                    all_neighborhoods.append(n)

        # Use params from first call, override neighborhood with merged list
        base_args = parsed[0].copy()
        base_args["neighborhood"] = ",".join(all_neighborhoods)

        # Build a synthetic merged tool call using the first call's ID
        import copy
        merged_tc = copy.deepcopy(search_calls[0])
        merged_tc.function.arguments = json.dumps(base_args, ensure_ascii=False)

        logger.info(
            "Merged %d search_properties calls into one: neighborhoods=%r",
            len(search_calls), all_neighborhoods,
        )

        # Return the merged search call + a synthetic "skipped" response for the
        # other search call IDs so the API gets a tool result for every call ID.
        skipped_ids = [tc.id for tc in search_calls[1:]]
        return [merged_tc] + other_calls, skipped_ids

    MAX_TOOL_ROUNDS = 8
    for _ in range(MAX_TOOL_ROUNDS):
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages,
            tools=get_tools(),
            tool_choice="auto",
        )
        assistant_msg = response.choices[0].message
        messages.append(assistant_msg.model_dump(exclude_none=True))

        if not assistant_msg.tool_calls:
            return (assistant_msg.content or "تحت أمرك.", [])

        # Merge multiple search_properties calls into one if needed
        tool_calls = assistant_msg.tool_calls
        skipped_ids: list[str] = []
        merge_result = _merge_search_calls(tool_calls)
        if isinstance(merge_result, tuple):
            tool_calls, skipped_ids = merge_result

        # Add dummy tool results for skipped (merged) call IDs so OpenAI
        # doesn't complain about missing tool responses.
        for skipped_id in skipped_ids:
            messages.append({
                "role": "tool",
                "tool_call_id": skipped_id,
                "name": "search_properties",
                "content": json.dumps({"merged": True}, ensure_ascii=False),
            })

        for tool_call in tool_calls:
            fn_name = tool_call.function.name
            try:
                fn_args = json.loads(tool_call.function.arguments or "{}")
            except json.JSONDecodeError:
                fn_args = {}

            logger.info("Tool requested: %s | args: %s", fn_name, fn_args)

            fn = available_functions.get(fn_name)
            if fn is None:
                tool_result = {"error": f"Unknown tool: {fn_name}"}
            else:
                try:
                    tool_result = fn(**fn_args)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Tool %s raised", fn_name)
                    tool_result = {"error": str(exc)}

            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "name": fn_name,
                "content": json.dumps(tool_result, ensure_ascii=False, default=str),
            })

    logger.warning("Exceeded tool-call rounds for %s", phone)
    return ("معذرة، حصل خطأ بسيط. ممكن تعيد طلبك؟", [])
