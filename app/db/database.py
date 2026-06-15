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


def search_properties(neighborhood: str, max_budget: int, min_budget: int = 0):
    """Search approved properties by neighborhood name and monthly budget (SAR).

    Joins to buildings and matches the neighborhood keyword against building.name.
    Returns up to 10 results sorted by price ascending.
    """
    min_budget = int(min_budget) if min_budget is not None else 0
    max_budget = int(max_budget) if max_budget is not None else 10 ** 9

    neighborhood_term = f"%{neighborhood}%"

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
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
                    p.whatsapp_number
                FROM properties p
                LEFT JOIN buildings b ON b.id = p.building_id
                WHERE p.status = 'approved'
                  AND (
                        b.name LIKE :nb
                     OR p.title LIKE :nb
                  )
                  AND p.price_monthly BETWEEN :min_b AND :max_b
                ORDER BY p.price_monthly ASC
                LIMIT 10
                """
            ),
            {
                "nb": neighborhood_term,
                "min_b": min_budget,
                "max_b": max_budget,
            },
        ).mappings().all()

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

    logger.info(
        "DB search: neighborhood=%r budget %s-%s SAR → %d results",
        neighborhood, min_budget, max_budget, len(results),
    )
    return results
