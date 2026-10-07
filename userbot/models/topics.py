# ─────────────────────────────────────────────────────────────────────────────
#  models / topics.py
#
#  PostgreSQL table: topics
#
#  Schema
#  ──────
#    id               BIGSERIAL PRIMARY KEY
#    url_key          VARCHAR(100) NOT NULL  (e.g. instagram, telegram, facebook, x)
#    name             VARCHAR(150) NOT NULL  (e.g. "Instagram Topics")
#    source_url       TEXT         NOT NULL  (source Telegram channel to read FROM)
#    url              TEXT         NOT NULL  (destination Telegram link to forward TO)
#    enabled          BOOLEAN      NOT NULL DEFAULT TRUE
#    interval_seconds INTEGER      NOT NULL DEFAULT 60
#    priority         INTEGER      NOT NULL DEFAULT 0
#    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW()
#    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW()
#
#  Constraint: UNIQUE(url_key, url)
#  Index:      idx_topics_url_key ON topics(url_key)
# ─────────────────────────────────────────────────────────────────────────────

from config.db import db, get_cursor
from config.migrations import run_auto_migrations


def bootstrap_topics_table() -> None:
    """Run auto-migrations for topics table, including source_url column."""
    run_auto_migrations()
    # Ensure source_url column exists for older installs
    from config.db import get_cursor, db as _db

    _cur = get_cursor()
    _cur.execute("""
        ALTER TABLE topics ADD COLUMN IF NOT EXISTS source_url TEXT NOT NULL DEFAULT '';
    """)
    _db.commit()
    _cur.close()


def _row_to_dict(row: tuple) -> dict:
    # Columns: id, url_key, name, source_url, url, enabled, interval_seconds, priority, created_at, updated_at
    return {
        "id": row[0],
        "url_key": row[1],
        "name": row[2],
        "source_url": row[3],
        "url": row[4],
        "enabled": row[5],
        "interval_seconds": row[6],
        "priority": row[7],
        "created_at": row[8],
        "updated_at": row[9],
        # Compatibility aliases
        "topic_name": row[2],
        "topic_urls": [{"url": row[4], "enabled": row[5], "interval_seconds": row[6]}],
    }


_SELECT = """
    SELECT id, url_key, name, source_url, url, enabled, interval_seconds, priority, created_at, updated_at
    FROM topics
"""


def create_topic_entry(
    url_key: str,
    name: str,
    url: str,
    source_url: str = "",
    enabled: bool = True,
    interval_seconds: int = 60,
    priority: int = 0,
) -> dict:
    """Insert or update a topic URL entry (destination) with UNIQUE(url_key, url).

    source_url: the Telegram channel/group to read messages FROM.
    url:        the Telegram channel/group to forward messages TO.
    """
    clean_key = url_key.strip().lower().replace(" ", "_")
    clean_src = source_url.strip() if source_url else ""
    cursor = get_cursor()
    cursor.execute(
        """
        INSERT INTO topics (url_key, name, source_url, url, enabled, interval_seconds, priority)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (url_key, url) DO UPDATE
            SET name             = EXCLUDED.name,
                source_url       = COALESCE(NULLIF(EXCLUDED.source_url, ''), topics.source_url),
                enabled          = EXCLUDED.enabled,
                interval_seconds = EXCLUDED.interval_seconds,
                priority         = EXCLUDED.priority,
                updated_at       = NOW()
        RETURNING id, url_key, name, source_url, url, enabled, interval_seconds, priority, created_at, updated_at;
    """,
        (clean_key, name, clean_src, url, enabled, interval_seconds, priority),
    )
    row = cursor.fetchone()
    db.commit()
    cursor.close()
    return _row_to_dict(row)


def get_all_topics() -> list[dict]:
    cursor = get_cursor()
    cursor.execute(_SELECT + " ORDER BY priority DESC, created_at DESC;")
    rows = cursor.fetchall()
    cursor.close()
    return [_row_to_dict(r) for r in rows]


def get_topics_by_key(url_key: str) -> list[dict]:
    cursor = get_cursor()
    cursor.execute(
        _SELECT + " WHERE url_key = %s ORDER BY priority DESC, id ASC;", (url_key,)
    )
    rows = cursor.fetchall()
    cursor.close()
    return [_row_to_dict(r) for r in rows]


def get_topic_keys_summary() -> list[dict]:
    """Get list of distinct topic url_keys with name, source_url, url count, and active count."""
    cursor = get_cursor()
    cursor.execute("""
        SELECT url_key, name,
               MAX(source_url) as source_url,
               COUNT(*) as total_urls,
               COUNT(*) FILTER (WHERE enabled = true) as active_urls
        FROM topics
        GROUP BY url_key, name
        ORDER BY name ASC;
    """)
    rows = cursor.fetchall()
    cursor.close()
    return [
        {
            "url_key": r[0],
            "name": r[1],
            "source_url": r[2] or "",
            "total_urls": r[3],
            "active_urls": r[4],
        }
        for r in rows
    ]


def get_topic_by_id(topic_id: int) -> dict | None:
    cursor = get_cursor()
    cursor.execute(_SELECT + " WHERE id = %s;", (topic_id,))
    row = cursor.fetchone()
    cursor.close()
    return _row_to_dict(row) if row else None


def update_topic_entry(
    topic_id: int,
    name: str | None = None,
    source_url: str | None = None,
    url: str | None = None,
    enabled: bool | None = None,
    interval_seconds: int | None = None,
    priority: int | None = None,
) -> dict | None:
    cursor = get_cursor()
    fields = []
    vals = []

    if name is not None:
        fields.append("name = %s")
        vals.append(name)
    if source_url is not None:
        fields.append("source_url = %s")
        vals.append(source_url)
    if url is not None:
        fields.append("url = %s")
        vals.append(url)
    if enabled is not None:
        fields.append("enabled = %s")
        vals.append(enabled)
    if interval_seconds is not None:
        fields.append("interval_seconds = %s")
        vals.append(interval_seconds)
    if priority is not None:
        fields.append("priority = %s")
        vals.append(priority)

    if not fields:
        return get_topic_by_id(topic_id)

    fields.append("updated_at = NOW()")
    vals.append(topic_id)

    cursor.execute(
        f"UPDATE topics SET {', '.join(fields)} WHERE id = %s RETURNING id, url_key, name, source_url, url, enabled, interval_seconds, priority, created_at, updated_at;",
        vals,
    )
    row = cursor.fetchone()
    db.commit()
    cursor.close()
    return _row_to_dict(row) if row else None


def delete_topic_entry(topic_id: int) -> bool:
    cursor = get_cursor()
    cursor.execute("DELETE FROM topics WHERE id = %s;", (topic_id,))
    deleted = cursor.rowcount > 0
    db.commit()
    cursor.close()
    return deleted


def delete_topics_by_key(url_key: str) -> bool:
    cursor = get_cursor()
    cursor.execute("DELETE FROM topics WHERE url_key = %s;", (url_key,))
    deleted = cursor.rowcount > 0
    db.commit()
    cursor.close()
    return deleted


# Legacy backward-compatible aliases
def create_topic(name: str, urls: list = []) -> dict:
    """Legacy helper: create topic with first URL as destination (no source)."""
    return create_topic_entry(
        url_key=name.lower().replace(" ", "_"),
        name=name,
        url=urls[0] if urls else "https://t.me/placeholder",
        source_url="",
    )


delete_topic = delete_topics_by_key
