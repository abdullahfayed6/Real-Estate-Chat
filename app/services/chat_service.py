import json
import re

from openai import OpenAI

from app.core.config import OPENAI_API_KEY, OPENAI_MODEL, logger
from app.services.prompts import get_system_prompt, get_tools
from app.db.seed_data import get_config
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
    if not apartment_type or apartment_type == "any":
        return properties
    return [p for p in properties if detect_type(p.get("title"), p.get("description")) == apartment_type]

# Process-memory sessions: phone → list of chat messages
SESSIONS: dict[str, list[dict]] = {}

# Separate store for search result state (not mixed into message history)
# phone → {"results": [...], "index": int}
SEARCH_STATE: dict[str, dict] = {}

# Keep search slots separate from the LLM's free-form conversation history.
CRITERIA: dict[str, dict] = {}

_DIRECTION_AREAS = {
    "شمال": ("العارض", "العقيق", "النرجس"), "شرق": ("المونسية",),
    "غرب": ("العريجاء",), "وسط": ("الملز",), "جنوب": ("منفوحة",),
}


def _normalise_arabic(value: str) -> str:
    return re.sub(r"[\u064b-\u0652ـ]", "", value.lower()).translate(
        str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي", "ة": "ه"})
    )


def _update_criteria(phone: str, message: str) -> dict:
    """Extract deterministic slots so requests work in any word order."""
    criteria = CRITERIA.setdefault(phone, {})
    text = _normalise_arabic(message.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")))
    areas = get_config()["neighborhood_groups"]
    normalised_areas = {_normalise_arabic(area): area for area in areas}
    direction_areas: list[str] = []
    for direction, candidates in _DIRECTION_AREAS.items():
        if re.search(rf"(?<!\w){direction}(?!\w)", text):
            expected = {_normalise_arabic(x) for x in candidates}
            direction_areas = [area for area in areas if _normalise_arabic(area) in expected]
            break

    generic_any = any(x in text for x in ("اي شيء", "اي شئ", "اي شي", "اي نوع", "ما يفرق النوع", "مايفرق النوع"))
    # "أي شيء" answers the question that is currently missing. If the type is
    # already known, it naturally means the customer has no budget limit.
    if generic_any and criteria.get("apartment_type") and "max_budget" not in criteria:
        criteria["min_budget"], criteria["max_budget"] = 0, 999999
    elif generic_any:
        criteria["apartment_type"] = "any"
    elif "غرفتين" in text or re.search(r"(?<!\d)2\s*غرف", text):
        criteria["apartment_type"] = "two_bedroom"
    elif "استوديو" in text:
        criteria["apartment_type"] = "studio"
    elif ("غرفه" in text or "اوضه" in text or re.search(r"(?<!\d)1\s*غرف", text)) and "صاله" in text:
        criteria["apartment_type"] = "one_bedroom"

    unrestricted_area = any(x in text for x in (
        "اي حي", "اي مكان", "كل الاحياء", "كل حي", "وين ما كان", "كل الرياض", "الرياض كلها",
    ))
    if unrestricted_area:
        selected = direction_areas or areas
        criteria["neighborhood"] = ",".join(selected)
        criteria["area_scope"] = "all neighborhoods in the requested direction" if direction_areas else "all available neighborhoods"
    elif "جرير" in text:
        criteria["neighborhood"], criteria["area_scope"] = "الملز", "الملز (جرير)"
    else:
        selected = [area for key, area in normalised_areas.items() if key in text]
        if not selected:
            selected = direction_areas
        if selected:
            criteria["neighborhood"] = ",".join(dict.fromkeys(selected))
            criteria["area_scope"] = "customer location preference"

    if any(x in text for x in ("اغلي", "اعلي سعر", "بسعر اعلي")):
        criteria["sort_order"] = "desc"
        criteria["min_budget"], criteria["max_budget"] = 0, 999999
        SEARCH_STATE.pop(phone, None)  # This is a new search, never the next old result.
    elif any(x in text for x in ("ارخص", "اوفرا", "اوفر")):
        criteria["sort_order"] = "asc"
        SEARCH_STATE.pop(phone, None)

    excluded = [
        area for key, area in normalised_areas.items()
        if re.search(rf"(?:ما\s*ابي|مو|مش|غير|بدون)\s*(?:في\s*)?.{{0,12}}{re.escape(key)}", text)
    ]
    if excluded:
        criteria["excluded_neighborhoods"] = list(dict.fromkeys(excluded))
        chosen = [n.strip() for n in criteria.get("neighborhood", "").split(",") if n.strip()]
        remaining = [n for n in chosen if n not in criteria["excluded_neighborhoods"]]
        if remaining:
            criteria["neighborhood"] = ",".join(remaining)

    if "max_budget" not in criteria and any(x in text for x in (
        "اي سعر", "باي سعر", "اي ميزانيه", "باي ميزانيه", "السعر ما يفرق",
        "مفتوحه الميزانيه", "الميزانيه مفتوحه", "بدون حد", "ايجار مفتوح",
    )):
        criteria["min_budget"], criteria["max_budget"] = 0, 999999
    else:
        # Never mistake "2 غرف" / "غرفة 1" for a monthly budget.
        budget_text = re.sub(r"(?<!\d)[12]\s*غرف\w*|غرف\w*\s*[12](?!\d)", "", text)
        numbers = [int(n) for n in re.findall(r"\d+", budget_text)]
        if len(numbers) >= 2 and ("بين" in budget_text or re.search(r"من\s*\d+\s*(?:الى|الي|ل)\s*\d+", budget_text)):
            criteria["min_budget"], criteria["max_budget"] = min(numbers[:2]), max(numbers[:2])
        elif numbers:
            amount = numbers[-1]
            criteria["min_budget"], criteria["max_budget"] = (amount, 999999) if any(x in text for x in ("فوق", "اكثر", "اعلي")) else (0, amount)
    return criteria


def _criteria_context(criteria: dict) -> str:
    fields = {key: criteria.get(key, "MISSING") for key in ("neighborhood", "area_scope", "excluded_neighborhoods", "apartment_type", "min_budget", "max_budget", "sort_order")}
    return (
        "INTERNAL CONFIRMED SEARCH STATE (never expose it): " + json.dumps(fields, ensure_ascii=False)
        + ". Values other than MISSING were already supplied by the customer. If neighborhood, apartment_type, and max_budget exist, call search_properties now; never ask for them again. If sort_order is desc or asc from the latest message, this is a NEW search; do not use next_property or any old result."
    )


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
    messages.append({"role": "system", "content": _criteria_context(_update_criteria(phone, message))})
    messages.append({"role": "user", "content": message})

    # ── Phone-scoped tool implementations ──────────────────────────────────────

    def _search(neighborhood: str, max_budget: int, min_budget: int = 0, apartment_type: str | None = None, sort_order: str = "asc") -> dict:
        """Run DB search, store all results, return only the FIRST property.

        *neighborhood* may be a single name or comma-separated names (for
        direction-based searches like "شمال").
        """
        # Split comma-separated neighborhoods into a list
        if "," in str(neighborhood):
            neighborhoods = [n.strip() for n in neighborhood.split(",") if n.strip()]
        else:
            neighborhoods = neighborhood

        excluded = set(CRITERIA.get(phone, {}).get("excluded_neighborhoods", []))
        if excluded:
            candidates = [neighborhoods] if isinstance(neighborhoods, str) else neighborhoods
            neighborhoods = [n for n in candidates if n not in excluded]
            if not neighborhoods:
                return {"found": 0, "error": "All requested neighborhoods are excluded by the customer."}

        # Map apartment_type to rooms_count filter in SQL (first-level filtering)
        sql_rooms = None
        if apartment_type == "two_bedroom":
            sql_rooms = 2
        elif apartment_type in ("one_bedroom", "studio"):
            sql_rooms = 1

        # Fetch all results matching neighborhood and rooms_count
        # Use high budget to calculate the accurate price range over all stock
        # Fetch the full candidate set before applying the exact type filter.
        # Limiting SQL first caused false "no availability" answers when a match
        # (such as Al Olaya) appeared after the first 10 rows.
        all_results = search_properties(neighborhoods, max_budget=999999, min_budget=0, rooms_count=sql_rooms, limit=None, sort_order=sort_order)

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

        excluded = set(CRITERIA.get(phone, {}).get("excluded_neighborhoods", []))
        if excluded:
            candidates = [neighborhoods] if isinstance(neighborhoods, str) else neighborhoods
            neighborhoods = [n for n in candidates if n not in excluded]
            if not neighborhoods:
                return {"found": 0, "error": "All requested neighborhoods are excluded by the customer."}

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
