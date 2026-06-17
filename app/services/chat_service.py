import json

from openai import OpenAI

from app.core.config import OPENAI_API_KEY, OPENAI_MODEL, logger
from app.services.prompts import get_system_prompt, get_tools
from app.db.database import search_properties, search_upcoming_properties

client = OpenAI(api_key=OPENAI_API_KEY)

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

    images: list[str] = []

    # ── Phone-scoped tool implementations ──────────────────────────────────────

    def _search(neighborhood: str, max_budget: int, min_budget: int = 0, rooms_count: int | None = None) -> dict:
        """Run DB search, store all results, return only the FIRST property.

        *neighborhood* may be a single name or comma-separated names (for
        direction-based searches like "شمال").
        """
        # Split comma-separated neighborhoods into a list
        if "," in str(neighborhood):
            neighborhoods = [n.strip() for n in neighborhood.split(",") if n.strip()]
        else:
            neighborhoods = neighborhood

        all_results = search_properties(neighborhoods, max_budget, min_budget, rooms_count)
        SEARCH_STATE[phone] = {"results": all_results, "index": 0}

        if not all_results:
            return {"found": 0}

        first = all_results[0]
        return {
            "total_found": len(all_results),
            "current_index": 1,
            "has_more": len(all_results) > 1,
            "property": first,
        }

    def _search_upcoming(
        neighborhood: str,
        max_budget: int,
        min_budget: int = 0,
        rooms_count: int | None = None,
        days_ahead: int = 30,
    ) -> dict:
        """Find apartments becoming available soon."""
        if "," in str(neighborhood):
            neighborhoods = [n.strip() for n in neighborhood.split(",") if n.strip()]
        else:
            neighborhoods = neighborhood

        results = search_upcoming_properties(
            neighborhoods, max_budget, min_budget, rooms_count, days_ahead
        )

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
            return (assistant_msg.content or "تحت أمرك.", images)

        for tool_call in assistant_msg.tool_calls:
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

            # Capture the image from whichever property was just surfaced
            if isinstance(tool_result, dict) and "property" in tool_result:
                prop = tool_result["property"]
                if prop.get("image_url"):
                    images = [prop["image_url"]]

            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "name": fn_name,
                "content": json.dumps(tool_result, ensure_ascii=False, default=str),
            })

    logger.warning("Exceeded tool-call rounds for %s", phone)
    return ("معذرة، حصل خطأ بسيط. ممكن تعيد طلبك؟", images)
