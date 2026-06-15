"""
Dynamic config loaded from the live database.
Results are cached for CACHE_TTL seconds so the DB isn't hit on every message.
The cache refreshes automatically — no app restart needed when data changes.
"""
import re
import time
import logging

logger = logging.getLogger("realestate-chat")

_cache: dict | None = None
_cache_time: float = 0
CACHE_TTL: int = 3600  # refresh every hour


def get_config() -> dict:
    """Return neighborhood list and price range, refreshing from DB if stale."""
    global _cache, _cache_time
    now = time.time()
    if _cache is None or (now - _cache_time) > CACHE_TTL:
        _cache = _load_from_db()
        _cache_time = now
        logger.info(
            "DB config refreshed: %d neighborhoods (%d groups), price range %s–%s SAR",
            len(_cache["neighborhoods"]),
            len(_cache["neighborhood_groups"]),
            _cache["price_min"],
            _cache["price_max"],
        )
    return _cache


def _load_from_db() -> dict:
    from app.db.database import engine
    from sqlalchemy import text

    with engine.connect() as conn:
        # Distinct building names that have at least one approved property
        rows = conn.execute(
            text("""
                SELECT DISTINCT b.name
                FROM buildings b
                JOIN properties p ON p.building_id = b.id
                WHERE p.status = 'approved'
                  AND b.name IS NOT NULL
                ORDER BY b.name
            """)
        ).fetchall()
        neighborhoods = [r[0] for r in rows if r[0]]

        # Price range across all approved listings
        row = conn.execute(
            text("""
                SELECT
                    MIN(price_monthly) AS min_price,
                    MAX(price_monthly) AS max_price
                FROM properties
                WHERE status = 'approved'
                  AND price_monthly IS NOT NULL
            """)
        ).fetchone()

    price_min = int(row[0]) if row and row[0] is not None else 0
    price_max = int(row[1]) if row and row[1] is not None else 99999

    # Build unique base names by stripping trailing numbers
    # e.g. "النرجس 1" and "النرجس 3" → "النرجس"
    seen: set = set()
    grouped: list = []
    for name in neighborhoods:
        base = re.sub(r'\s+\d+$', '', name).strip()
        if base not in seen:
            seen.add(base)
            grouped.append(base)

    return {
        "city": "الرياض",
        "neighborhoods": neighborhoods,        # full names used for DB search
        "neighborhood_groups": grouped,        # base names shown to user
        "price_min": price_min,
        "price_max": price_max,
    }
