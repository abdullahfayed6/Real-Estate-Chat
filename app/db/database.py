import json

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app.core.config import DATABASE_URL, logger

# Read-only engine — we never create or modify tables.
engine: Engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)


def _format_whatsapp_url(number: str | None) -> str | None:
    """Convert a local Saudi number like 0555458305 → https://wa.me/966555458305"""
    if not number:
        return None
    cleaned = number.strip().lstrip("+")
    # Remove leading country code if already present, then rebuild
    if cleaned.startswith("966"):
        local = cleaned[3:]
    elif cleaned.startswith("0"):
        local = cleaned[1:]
    else:
        local = cleaned
    return f"https://wa.me/966{local}"


def _format_rows(rows) -> list[dict]:
    """Shared helper: convert raw DB rows into clean dicts for the API."""
    results = []
    for row in rows:
        r = dict(row)

        # Parse images JSON array and pick the first usable URL.
        raw_images = r.pop("images", None)
        image_url = None
        if raw_images:
            try:
                imgs = json.loads(raw_images) if isinstance(raw_images, str) else raw_images
                if isinstance(imgs, list) and imgs:
                    first = imgs[0]
                    if isinstance(first, str):
                        image_url = (
                            first if first.startswith("http")
                            else f"https://mashimarketing.com/storage/{first}"
                        )
                    elif isinstance(first, dict):
                        image_url = first.get("url") or first.get("path")
            except (json.JSONDecodeError, TypeError):
                pass
        r["image_url"] = image_url

        # Format WhatsApp URL correctly (Saudi country code)
        r["whatsapp_url"] = _format_whatsapp_url(r.get("whatsapp_number"))

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
            p.images,
            p.rooms_count,
            p.baths_count,
            p.area,
            p.whatsapp_number,
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
            p.images,
            p.rooms_count,
            p.baths_count,
            p.area,
            p.whatsapp_number,
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
