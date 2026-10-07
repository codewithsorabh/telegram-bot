import os
import psycopg2
import redis
from dotenv import load_dotenv
from config.logger import LOG, log

load_dotenv()

# ─────────────────────────────────────────────
#  PostgreSQL Connection
# ─────────────────────────────────────────────
def _connect_db():
    host = os.getenv("POSTGRES_HOST", "127.0.0.1")
    port = os.getenv("POSTGRES_PORT", "5432")
    dbname = os.getenv("POSTGRES_DB")
    user = os.getenv("POSTGRES_USER")
    password = os.getenv("POSTGRES_PASSWORD")
    try:
        return psycopg2.connect(host=host, port=port, database=dbname, user=user, password=password)
    except psycopg2.OperationalError:
        if host in ("postgres", "db", "localhost"):
            return psycopg2.connect(host="127.0.0.1", port=port, database=dbname, user=user, password=password)
        raise

db = _connect_db()

# ─────────────────────────────────────────────
#  Step 1 — pg_durable extension (best-effort)
# ─────────────────────────────────────────────
try:
    _ext_cur = db.cursor()
    _ext_cur.execute("CREATE EXTENSION IF NOT EXISTS pg_durable;")
    db.commit()
    _ext_cur.close()
except Exception as _e:
    db.rollback()
    LOG.warning("pg_durable extension skipped — %s", _e)

# ─────────────────────────────────────────────
#  Step 2 — Base table: telegram_users
# ─────────────────────────────────────────────
_tbl_cur = db.cursor()
_tbl_cur.execute("""
    CREATE TABLE IF NOT EXISTS telegram_users (
        id             SERIAL       PRIMARY KEY,
        added_by       BIGINT       NOT NULL DEFAULT 0,
        api_id         INTEGER      NOT NULL,
        api_hash       TEXT         NOT NULL,
        phone          TEXT         NOT NULL UNIQUE,
        session_string TEXT         NOT NULL,
        created_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW()
    );
""")
db.commit()
_tbl_cur.close()

# ─────────────────────────────────────────────
#  Step 3 — Shared trigger function _set_updated_at
#           (must exist before any model bootstrap
#            that installs BEFORE UPDATE triggers)
# ─────────────────────────────────────────────
_fn_cur = db.cursor()
_fn_cur.execute("""
    CREATE OR REPLACE FUNCTION _set_updated_at()
    RETURNS TRIGGER LANGUAGE plpgsql AS $$
    BEGIN
        NEW.updated_at = NOW();
        RETURN NEW;
    END;
    $$;
""")
db.commit()
_fn_cur.close()

# ─────────────────────────────────────────────
#  Redis Connection
# ─────────────────────────────────────────────
def _connect_redis():
    host = os.getenv("REDIS_HOST", "127.0.0.1")
    port = int(os.getenv("REDIS_PORT", "6379"))
    r_client = redis.Redis(host=host, port=port, decode_responses=True)
    try:
        r_client.ping()
        return r_client
    except Exception:
        if host in ("redis", "localhost"):
            return redis.Redis(host="127.0.0.1", port=port, decode_responses=True)
        return r_client

r = _connect_redis()

# ─────────────────────────────────────────────
#  Telegram Bot token
# ─────────────────────────────────────────────
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")


# ─────────────────────────────────────────────
#  Safe cursor factory
#
#  Always rolls back any pending failed
#  transaction before returning a fresh cursor.
#  This prevents psycopg2.errors.InFailedSqlTransaction
#  from poisoning the shared connection object.
# ─────────────────────────────────────────────
def get_cursor():
    """
    Return a fresh psycopg2 cursor.

    Rolls back any stuck/failed transaction on the shared
    `db` connection before creating the cursor, so that
    a single bad query never poisons all subsequent queries.
    """
    try:
        db.rollback()
    except Exception:
        pass
    return db.cursor()

