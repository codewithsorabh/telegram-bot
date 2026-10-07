# ─────────────────────────────────────────────────────────────────────────────
#  models / forwarding_state.py
#
#  Tracks runtime forwarding status per account × topic × destination URL.
#
#  Schema
#  ──────
#    id                BIGSERIAL
#    telegram_user_id  BIGINT   FK → telegram_users(id)
#    topic_id          BIGINT   FK → topics(id)
#    url               TEXT     destination URL
#    last_message_id   BIGINT
#    last_sent_at      TIMESTAMPTZ
#    next_run_at       TIMESTAMPTZ
#    status            VARCHAR  success / failed / waiting / flood_wait
#    last_error        TEXT
#    failure_count     INTEGER
#    flood_wait_until  TIMESTAMPTZ
#    created_at        TIMESTAMPTZ
#    updated_at        TIMESTAMPTZ
#    UNIQUE (telegram_user_id, topic_id, url)
# ─────────────────────────────────────────────────────────────────────────────

from config.db import db, get_cursor
from config.logger import LOG


def bootstrap_forwarding_state_table() -> None:
    cursor = get_cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS forwarding_state (
            id                BIGSERIAL    PRIMARY KEY,
            telegram_user_id  BIGINT       NOT NULL REFERENCES telegram_users(id) ON DELETE CASCADE,
            topic_id          BIGINT       NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
            url               TEXT         NOT NULL,
            last_message_id   BIGINT,
            last_sent_at      TIMESTAMPTZ,
            next_run_at       TIMESTAMPTZ,
            status            VARCHAR(20)  NOT NULL DEFAULT 'waiting',
            last_error        TEXT,
            failure_count     INTEGER      NOT NULL DEFAULT 0,
            flood_wait_until  TIMESTAMPTZ,
            created_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            updated_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            UNIQUE (telegram_user_id, topic_id, url)
        );
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_fwdstate_user
        ON forwarding_state(telegram_user_id);
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_fwdstate_next_run
        ON forwarding_state(next_run_at);
    """)

    cursor.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_trigger WHERE tgname = 'trg_fwdstate_updated_at'
            ) THEN
                CREATE TRIGGER trg_fwdstate_updated_at
                BEFORE UPDATE ON forwarding_state
                FOR EACH ROW EXECUTE FUNCTION _set_updated_at();
            END IF;
        END $$;
    """)

    db.commit()
    cursor.close()
    LOG.info("forwarding_state table ready")


def upsert_state(
    telegram_user_id: int,
    topic_id: int,
    url: str,
    last_message_id: int | None = None,
    last_sent_at=None,
    next_run_at=None,
    status: str = "waiting",
    last_error: str | None = None,
    failure_count: int = 0,
    flood_wait_until=None,
) -> dict:
    """Insert or update forwarding state for a user+topic+url triple."""
    cursor = get_cursor()
    cursor.execute("""
        INSERT INTO forwarding_state
            (telegram_user_id, topic_id, url, last_message_id, last_sent_at,
             next_run_at, status, last_error, failure_count, flood_wait_until)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (telegram_user_id, topic_id, url) DO UPDATE
            SET last_message_id  = COALESCE(EXCLUDED.last_message_id, forwarding_state.last_message_id),
                last_sent_at     = COALESCE(EXCLUDED.last_sent_at,    forwarding_state.last_sent_at),
                next_run_at      = EXCLUDED.next_run_at,
                status           = EXCLUDED.status,
                last_error       = EXCLUDED.last_error,
                failure_count    = EXCLUDED.failure_count,
                flood_wait_until = EXCLUDED.flood_wait_until,
                updated_at       = NOW()
        RETURNING *;
    """, (
        telegram_user_id, topic_id, url,
        last_message_id, last_sent_at, next_run_at,
        status, last_error, failure_count, flood_wait_until,
    ))
    row = cursor.fetchone()
    db.commit()
    cursor.close()
    return _row_to_dict(row)


def get_state(telegram_user_id: int, topic_id: int, url: str) -> dict | None:
    cursor = get_cursor()
    cursor.execute("""
        SELECT * FROM forwarding_state
        WHERE telegram_user_id=%s AND topic_id=%s AND url=%s;
    """, (telegram_user_id, topic_id, url))
    row = cursor.fetchone()
    cursor.close()
    return _row_to_dict(row) if row else None


def get_states_for_user(telegram_user_id: int) -> list[dict]:
    cursor = get_cursor()
    cursor.execute("""
        SELECT * FROM forwarding_state
        WHERE telegram_user_id=%s
        ORDER BY topic_id, url;
    """, (telegram_user_id,))
    rows = cursor.fetchall()
    cursor.close()
    return [_row_to_dict(r) for r in rows]


def reset_failure_count(telegram_user_id: int, topic_id: int, url: str) -> None:
    cursor = get_cursor()
    cursor.execute("""
        UPDATE forwarding_state
        SET failure_count=0, last_error=NULL, status='waiting'
        WHERE telegram_user_id=%s AND topic_id=%s AND url=%s;
    """, (telegram_user_id, topic_id, url))
    db.commit()
    cursor.close()


def _row_to_dict(row: tuple) -> dict:
    return {
        "id":               row[0],
        "telegram_user_id": row[1],
        "topic_id":         row[2],
        "url":              row[3],
        "last_message_id":  row[4],
        "last_sent_at":     row[5],
        "next_run_at":      row[6],
        "status":           row[7],
        "last_error":       row[8],
        "failure_count":    row[9],
        "flood_wait_until": row[10],
        "created_at":       row[11],
        "updated_at":       row[12],
    }
