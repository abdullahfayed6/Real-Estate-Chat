from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app.core.config import DATABASE_URL, logger

# Read-only engine — we never create or modify tables.
engine: Engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)


# Invisible Unicode bidi/directional control chars that sometimes prefix phone
# numbers in the DB (e.g. U+202D). They corrupt display and must be removed.
_BIDI_CONTROLS = dict.fromkeys(
    [0x200E, 0x200F, 0x202A, 0x202B, 0x202C, 0x202D, 0x202E, 0x2066, 0x2067, 0x2068, 0x2069],
    None,
)


def _format_phone(number: str | None) -> str | None:
    """Return the supervisor phone normalised to +966 international format.

    Strips invisible bidi/directional control chars, spaces, dashes and other
    separators, then rebuilds the number as +966XXXXXXXXX regardless of whether
    it was stored locally (05xxxxxxxx), with a country code (966...), or already
    international (+966...).
    """
    if not number:
        return None

    # Remove bidi controls, then keep digits only (drops spaces, dashes, +, etc.)
    cleaned = str(number).translate(_BIDI_CONTROLS)
    digits = "".join(ch for ch in cleaned if ch.isdigit())
    if not digits:
        return None

    # Drop any leading zeros (local "05..." or international "00966..." prefix),
    # then strip the 966 country code if present, leaving the subscriber digits.
    digits = digits.lstrip("0")
    if digits.startswith("966"):
        digits = digits[3:]

    if not digits:
        return None
    return f"+966{digits}"


def _format_rows(rows) -> list[dict]:
    """Shared helper: convert raw DB rows into clean dicts for the API."""
    results = []
    for row in rows:
        r = dict(row)

        # Drop the raw images payload — image URLs are never surfaced anymore.
        r.pop("images", None)

        # Contact numbers sent when a customer accepts: supervisor first, guard
        # as the fallback if the supervisor doesn't answer.
        r["supervisor_phone"] = _format_phone(r.get("supervisor_phone"))
        r["guard_phone"] = _format_phone(r.get("guard_phone"))

        # Convert Decimal → float for JSON serialisation.
        for key in ("monthly_price", "price_semi_annual", "price_annual"):
            if r.get(key) is not None:
                r[key] = float(r[key])

        results.append(r)
    return results


def search_properties(
    neighborhoods: "list[str] | str",
    max_budget: int,
    min_budget: int = 0,
    rooms_count: int | None = None,
):
    """Search approved properties by neighborhood(s), budget, and optional room count.

    *neighborhoods* can be a single string OR a list of strings (used when the
    customer says a direction like "شمال" and we need to search multiple areas).
    """
    min_budget = int(min_budget) if min_budget is not None else 0
    max_budget = int(max_budget) if max_budget is not None else 10 ** 9

    # Normalise to a list
    if isinstance(neighborhoods, str):
        neighborhoods = [neighborhoods]

    # Build LIKE terms
    nb_terms = [f"%{nb}%" for nb in neighborhoods]

    # Dynamic WHERE clause for multiple neighborhoods
    nb_clauses = " OR ".join(
        f"b.name LIKE :nb{i} OR p.title LIKE :nb{i}" for i in range(len(nb_terms))
    )
    params: dict = {f"nb{i}": t for i, t in enumerate(nb_terms)}
    params["min_b"] = min_budget
    params["max_b"] = max_budget

    rooms_clause = ""
    if rooms_count is not None:
        rooms_clause = "AND p.rooms_count = :rooms"
        params["rooms"] = int(rooms_count)

    sql = f"""
        SELECT
            p.id,
            p.title,
            p.location,
            b.name          AS building_name,
            p.price_monthly AS monthly_price,
            p.price_semi_annual,
            p.price_annual,
            p.description,
            p.rooms_count,
            p.baths_count,
            p.area,
            p.supervisor_phone,
            p.guard_phone,
            p.canonical_url
        FROM properties p
        LEFT JOIN buildings b ON b.id = p.building_id
        WHERE p.status = 'approved'
          AND ({nb_clauses})
          AND p.price_monthly BETWEEN :min_b AND :max_b
          {rooms_clause}
        ORDER BY p.price_monthly ASC
        LIMIT 10
    """

    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).mappings().all()

    results = _format_rows(rows)

    logger.info(
        "DB search: neighborhoods=%r rooms=%s budget %s-%s SAR → %d results",
        neighborhoods, rooms_count, min_budget, max_budget, len(results),
    )
    return results


def search_upcoming_properties(
    neighborhoods: "list[str] | str",
    max_budget: int,
    min_budget: int = 0,
    rooms_count: int | None = None,
    days_ahead: int = 30,
):
    """Find properties whose current rental ends within *days_ahead* days.

    These are apartments that will become available soon — useful when no
    currently-available apartment matches the customer's criteria.
    """
    min_budget = int(min_budget) if min_budget is not None else 0
    max_budget = int(max_budget) if max_budget is not None else 10 ** 9

    if isinstance(neighborhoods, str):
        neighborhoods = [neighborhoods]

    nb_terms = [f"%{nb}%" for nb in neighborhoods]
    nb_clauses = " OR ".join(
        f"b.name LIKE :nb{i} OR p.title LIKE :nb{i}" for i in range(len(nb_terms))
    )
    params: dict = {f"nb{i}": t for i, t in enumerate(nb_terms)}
    params["min_b"] = min_budget
    params["max_b"] = max_budget
    params["days"] = int(days_ahead)

    rooms_clause = ""
    if rooms_count is not None:
        rooms_clause = "AND p.rooms_count = :rooms"
        params["rooms"] = int(rooms_count)

    sql = f"""
        SELECT
            p.id,
            p.title,
            p.location,
            b.name          AS building_name,
            p.price_monthly AS monthly_price,
            p.price_semi_annual,
            p.price_annual,
            p.description,
            p.rooms_count,
            p.baths_count,
            p.area,
            p.supervisor_phone,
            p.guard_phone,
            p.canonical_url,
            p.rented_until
        FROM properties p
        LEFT JOIN buildings b ON b.id = p.building_id
        WHERE p.status = 'approved'
          AND p.rented_until IS NOT NULL
          AND p.rented_until BETWEEN CURDATE() AND DATE_ADD(CURDATE(), INTERVAL :days DAY)
          AND ({nb_clauses})
          AND p.price_monthly BETWEEN :min_b AND :max_b
          {rooms_clause}
        ORDER BY p.rented_until ASC
        LIMIT 5
    """

    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).mappings().all()

    results = _format_rows(rows)

    # Add days_until_available to each result
    from datetime import date
    today = date.today()
    for r in results:
        if r.get("rented_until"):
            delta = r["rented_until"] - today
            r["days_until_available"] = max(delta.days, 0)
            r["rented_until"] = str(r["rented_until"])

    logger.info(
        "DB upcoming: neighborhoods=%r rooms=%s budget %s-%s SAR within %d days → %d results",
        neighborhoods, rooms_count, min_budget, max_budget, days_ahead, len(results),
    )
    return results
