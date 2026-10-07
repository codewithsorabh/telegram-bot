# ─────────────────────────────────────────────
#  models / forwarding_schedule.py
#
#  Tracks first-run schedule per user × url_key × url.
#  Used to enforce a 30-second global delay on the very
#  first forward for each URL. After the first run,
#  interval scheduling is controlled by forwarding_state.
#
#  Schema
#  ──────
#    id          BIGSERIAL  PK
#    user_id     BIGINT     FK → telegram_users(id)
#    url_key     TEXT       topic key (e.g. "telegram")
#    url         TEXT       destination URL
#    is_run_at   TIMESTAMPTZ  time when first forward was done (NULL = never run)
#    created_at  TIMESTAMPTZ
#    updated_at  TIMESTAMPTZ
#    UNIQUE (user_id, url_key, url)
# ─────────────────────────────────────────────

from config.db import db, get_cursor
from config.logger import LOG


def bootstrap_forwarding_schedule_table() -> None:
    cursor = get_cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS forwarding_schedule (
            id          BIGSERIAL    PRIMARY KEY,
            user_id     BIGINT       NOT NULL REFERENCES telegram_users(id) ON DELETE CASCADE,
            url_key     TEXT         NOT NULL,
            url         TEXT         NOT NULL,
            is_run_at   TIMESTAMPTZ,
            created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            UNIQUE (user_id, url_key, url)
        );
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_fwdsched_user
        ON forwarding_schedule(user_id);
    """)
    db.commit()
    cursor.close()
    LOG.info("forwarding_schedule table ready")


def has_been_run(user_id: int, url_key: str, url: str) -> bool:
    """Return True if this user+url_key+url has been forwarded at least once."""
    cursor = get_cursor()
    cursor.execute("""
        SELECT is_run_at FROM forwarding_schedule
        WHERE user_id=%s AND url_key=%s AND url=%s;
    """, (user_id, url_key, url))
    row = cursor.fetchone()
    cursor.close()
    if row is None:
        return False
    return row[0] is not None


def mark_run(user_id: int, url_key: str, url: str) -> None:
    """Insert or update the forwarding_schedule entry, setting is_run_at = NOW()."""
    cursor = get_cursor()
    cursor.execute("""
        INSERT INTO forwarding_schedule (user_id, url_key, url, is_run_at)
        VALUES (%s, %s, %s, NOW())
        ON CONFLICT (user_id, url_key, url) DO UPDATE
            SET is_run_at  = EXCLUDED.is_run_at,
                updated_at = NOW();
    """, (user_id, url_key, url))
    db.commit()
    cursor.close()
