# ─────────────────────────────────────────────────────────────────────────────
#  models / channel_joins.py
#
#  Tracks channel auto-joins, pending join requests (with 1000s resend),
#  and @beem bot verification state.
#
#  Schema
#  ──────
#    id                BIGSERIAL    PRIMARY KEY
#    telegram_user_id  BIGINT       NOT NULL REFERENCES telegram_users(id) ON DELETE CASCADE
#    channel_target    TEXT         NOT NULL (username, invite hash, or link)
#    status            VARCHAR(30)  NOT NULL DEFAULT 'pending'
#                                   ('pending', 'joined', 'verified', 'failed')
#    join_type         VARCHAR(30)  NOT NULL DEFAULT 'auto_join'
#                                   ('auto_join', 'beem_verification', 'join_request')
#    requested_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
#    last_attempt_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
#    joined_at         TIMESTAMPTZ
#    retry_count       INTEGER      NOT NULL DEFAULT 0
#    last_error        TEXT
#    created_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW()
#    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW()
#    UNIQUE (telegram_user_id, channel_target)
# ─────────────────────────────────────────────────────────────────────────────

import re
from typing import Any
from datetime import datetime, timezone
try:
    from config.db import db, get_cursor
    from config.logger import LOG
except ImportError:
    from userbot.config.db import db, get_cursor
    from userbot.config.logger import LOG


# ─────────────────────────────────────────────────────────────────────────────
#  Topic URL & Base Join Target Extractor
# ─────────────────────────────────────────────────────────────────────────────
_RE_PRIV_TOPIC = re.compile(r"^(?:https?://)?(?:t\.me|telegram\.me|telegram\.dog)/c/(\d+)(?:/\d+)+$", re.I)
_RE_PRIV_BASE = re.compile(r"^(?:https?://)?(?:t\.me|telegram\.me|telegram\.dog)/c/(\d+)$", re.I)
_RE_INVITE = re.compile(r"^(?:https?://)?(?:t\.me|telegram\.me|telegram\.dog)/(?:\+|joinchat/)([A-Za-z0-9_-]+)$", re.I)
_RE_PUB_TOPIC = re.compile(r"^(?:https?://)?(?:t\.me|telegram\.me|telegram\.dog)/([A-Za-z0-9_]{4,})(?:/\d+)+$", re.I)
_RE_PUB_BASE = re.compile(r"^(?:https?://)?(?:t\.me|telegram\.me|telegram\.dog)/([A-Za-z0-9_]{4,})$", re.I)
_RE_AT_TOPIC = re.compile(r"^@([A-Za-z0-9_]{4,})(?:/\d+)+$")
_RE_AT_BASE = re.compile(r"^@([A-Za-z0-9_]{4,})$")
_RE_RAW_PEER = re.compile(r"^-100(\d{7,})$")
_RE_RAW_GID = re.compile(r"^(\d{7,})$")


def extract_base_join_target(target: Any) -> dict:
    """
    Extract the parent channel/group join target, stripping any topic sub-IDs.

    Returns a dict with:
        base_url: Canonical base join URL (e.g. 'https://t.me/c/1234567890' or 'https://t.me/channel')
        clean_target: Short base handle or target (e.g. '@channel' or 'c/1234567890')
        username: Clean channel username if public (e.g. 'channel')
        group_id: Raw group_id if private (e.g. 1234567890)
        peer_id: Telethon peer ID (-100... for private supergroups)
        invite_hash: Invite hash if invite link (e.g. 'AbCdEf')
        is_topic: True if the input was a topic URL with sub-ID
        candidates: List of strings that represent this entity in DB or Telethon
    """
    if isinstance(target, dict):
        raw = str(target.get("raw") or target.get("url") or target.get("normalized") or "").strip()
    else:
        raw = str(target).strip()

    s = raw.split("?")[0].rstrip("/")

    # 1. Private topic (t.me/c/1234567890/123 or t.me/c/1234567890/123/456)
    m = _RE_PRIV_TOPIC.match(s)
    if m:
        gid = m.group(1)
        base = f"https://t.me/c/{gid}"
        peer_id = int(f"-100{gid}")
        candidates = [base, f"t.me/c/{gid}", f"-100{gid}", gid, f"c/{gid}", raw, s]
        return {
            "base_url": base,
            "clean_target": f"c/{gid}",
            "username": None,
            "group_id": int(gid),
            "peer_id": peer_id,
            "invite_hash": None,
            "is_topic": True,
            "candidates": list(dict.fromkeys(candidates)),
        }

    # 2. Private base chat (t.me/c/1234567890)
    m = _RE_PRIV_BASE.match(s)
    if m:
        gid = m.group(1)
        base = f"https://t.me/c/{gid}"
        peer_id = int(f"-100{gid}")
        candidates = [base, f"t.me/c/{gid}", f"-100{gid}", gid, f"c/{gid}", raw, s]
        return {
            "base_url": base,
            "clean_target": f"c/{gid}",
            "username": None,
            "group_id": int(gid),
            "peer_id": peer_id,
            "invite_hash": None,
            "is_topic": False,
            "candidates": list(dict.fromkeys(candidates)),
        }

    # 3. Invite link (t.me/+hash or t.me/joinchat/hash)
    m = _RE_INVITE.match(s)
    if m:
        h = m.group(1)
        base = f"https://t.me/+{h}"
        candidates = [base, f"t.me/+{h}", f"+{h}", h, raw, s]
        return {
            "base_url": base,
            "clean_target": f"+{h}",
            "username": None,
            "group_id": None,
            "peer_id": None,
            "invite_hash": h,
            "is_topic": False,
            "candidates": list(dict.fromkeys(candidates)),
        }

    # 4. Public topic (t.me/groupname/123 or t.me/groupname/123/456)
    m = _RE_PUB_TOPIC.match(s)
    if m:
        u = m.group(1).lower()
        base = f"https://t.me/{u}"
        candidates = [f"@{u}", u, base, f"t.me/{u}", raw, s]
        return {
            "base_url": base,
            "clean_target": f"@{u}",
            "username": u,
            "group_id": None,
            "peer_id": None,
            "invite_hash": None,
            "is_topic": True,
            "candidates": list(dict.fromkeys(candidates)),
        }

    # 5. Public base (t.me/groupname)
    m = _RE_PUB_BASE.match(s)
    if m:
        u = m.group(1).lower()
        base = f"https://t.me/{u}"
        candidates = [f"@{u}", u, base, f"t.me/{u}", raw, s]
        return {
            "base_url": base,
            "clean_target": f"@{u}",
            "username": u,
            "group_id": None,
            "peer_id": None,
            "invite_hash": None,
            "is_topic": False,
            "candidates": list(dict.fromkeys(candidates)),
        }

    # 6. At topic (@groupname/123)
    m = _RE_AT_TOPIC.match(s)
    if m:
        u = m.group(1).lower()
        base = f"https://t.me/{u}"
        candidates = [f"@{u}", u, base, f"t.me/{u}", raw, s]
        return {
            "base_url": base,
            "clean_target": f"@{u}",
            "username": u,
            "group_id": None,
            "peer_id": None,
            "invite_hash": None,
            "is_topic": True,
            "candidates": list(dict.fromkeys(candidates)),
        }

    # 7. At base (@groupname)
    m = _RE_AT_BASE.match(s)
    if m:
        u = m.group(1).lower()
        base = f"https://t.me/{u}"
        candidates = [f"@{u}", u, base, f"t.me/{u}", raw, s]
        return {
            "base_url": base,
            "clean_target": f"@{u}",
            "username": u,
            "group_id": None,
            "peer_id": None,
            "invite_hash": None,
            "is_topic": False,
            "candidates": list(dict.fromkeys(candidates)),
        }

    # 8. Raw peer ID (-1001234567890)
    m = _RE_RAW_PEER.match(s)
    if m:
        gid = m.group(1)
        base = f"https://t.me/c/{gid}"
        candidates = [s, f"-100{gid}", gid, base, f"t.me/c/{gid}"]
        return {
            "base_url": base,
            "clean_target": s,
            "username": None,
            "group_id": int(gid),
            "peer_id": int(s),
            "invite_hash": None,
            "is_topic": False,
            "candidates": list(dict.fromkeys(candidates)),
        }

    # 9. Raw numeric group ID (1234567890)
    m = _RE_RAW_GID.match(s)
    if m:
        gid = m.group(1)
        base = f"https://t.me/c/{gid}"
        peer_id = int(f"-100{gid}")
        candidates = [f"-100{gid}", gid, base, f"t.me/c/{gid}", s]
        return {
            "base_url": base,
            "clean_target": s,
            "username": None,
            "group_id": int(gid),
            "peer_id": peer_id,
            "invite_hash": None,
            "is_topic": False,
            "candidates": list(dict.fromkeys(candidates)),
        }

    # Fallback
    return {
        "base_url": raw,
        "clean_target": raw,
        "username": None,
        "group_id": None,
        "peer_id": None,
        "invite_hash": None,
        "is_topic": False,
        "candidates": [raw, s],
    }


def cleanup_topic_sub_ids_in_channel_joins() -> int:
    """
    Cleans up any existing rows in channel_joins that have topic sub-IDs.
    Migrates them to the base parent target or removes duplicates.
    """
    cursor = get_cursor()
    cursor.execute("SELECT id, telegram_user_id, channel_target, status, join_type FROM channel_joins;")
    rows = cursor.fetchall()
    cleaned_count = 0

    for row in rows:
        row_id, uid, target, status, jtype = row
        info = extract_base_join_target(target)
        if info.get("is_topic"):
            base_target = info["base_url"].strip().lower()
            cursor.execute(
                "SELECT id, status FROM channel_joins WHERE telegram_user_id = %s AND LOWER(channel_target) = %s AND id != %s;",
                (uid, base_target, row_id),
            )
            existing = cursor.fetchone()
            if existing:
                exist_id, exist_status = existing
                if status in ("joined", "verified") and exist_status not in ("joined", "verified"):
                    cursor.execute(
                        "UPDATE channel_joins SET status = %s, join_type = %s, updated_at = NOW() WHERE id = %s;",
                        (status, jtype, exist_id),
                    )
                cursor.execute("DELETE FROM channel_joins WHERE id = %s;", (row_id,))
            else:
                cursor.execute(
                    "UPDATE channel_joins SET channel_target = %s, updated_at = NOW() WHERE id = %s;",
                    (base_target, row_id),
                )
            cleaned_count += 1

    db.commit()
    cursor.close()
    if cleaned_count > 0:
        LOG.info("🧹 Cleaned up %d channel_joins records with topic sub-IDs", cleaned_count)
    return cleaned_count


def bootstrap_channel_joins_table() -> None:
    """Ensure channel_joins table exists with proper indexes and update trigger."""
    cursor = get_cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS channel_joins (
            id                BIGSERIAL    PRIMARY KEY,
            telegram_user_id  BIGINT       NOT NULL REFERENCES telegram_users(id) ON DELETE CASCADE,
            channel_target    TEXT         NOT NULL,
            status            VARCHAR(30)  NOT NULL DEFAULT 'pending',
            join_type         VARCHAR(30)  NOT NULL DEFAULT 'auto_join',
            requested_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            last_attempt_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            joined_at         TIMESTAMPTZ,
            retry_count       INTEGER      NOT NULL DEFAULT 0,
            last_error        TEXT,
            created_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            updated_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            UNIQUE (telegram_user_id, channel_target)
        );
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_channel_joins_user_status
        ON channel_joins(telegram_user_id, status);
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_channel_joins_last_attempt
        ON channel_joins(last_attempt_at);
    """)

    cursor.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_trigger WHERE tgname = 'trg_channel_joins_updated_at'
            ) THEN
                CREATE TRIGGER trg_channel_joins_updated_at
                BEFORE UPDATE ON channel_joins
                FOR EACH ROW EXECUTE FUNCTION _set_updated_at();
            END IF;
        END $$;
    """)

    db.commit()
    cursor.close()
    LOG.info("channel_joins table ready")

    # Run auto-cleanup of any legacy topic sub-IDs
    try:
        cleanup_topic_sub_ids_in_channel_joins()
    except Exception as exc:
        LOG.debug("Notice during topic sub-ID cleanup: %s", exc)


def record_join_request(
    telegram_user_id: int,
    channel_target: Any,
    status: str = "pending",
    join_type: str = "auto_join",
    last_error: str | None = None,
) -> dict:
    """
    Record or update a channel join request under the parent chat base target (topic sub-IDs stripped).
    If the record already exists and is pending, increments retry_count and updates last_attempt_at.
    """
    info = extract_base_join_target(channel_target)
    clean_target = info["base_url"].strip().lower()
    now = datetime.now(timezone.utc)
    cursor = get_cursor()
    cursor.execute("""
        INSERT INTO channel_joins
            (telegram_user_id, channel_target, status, join_type, requested_at, last_attempt_at, last_error)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (telegram_user_id, channel_target) DO UPDATE
            SET status          = EXCLUDED.status,
                join_type       = EXCLUDED.join_type,
                last_attempt_at = EXCLUDED.last_attempt_at,
                last_error      = EXCLUDED.last_error,
                retry_count     = channel_joins.retry_count + 1,
                updated_at      = NOW()
        RETURNING id, telegram_user_id, channel_target, status, join_type, requested_at, last_attempt_at, joined_at, retry_count, last_error;
    """, (telegram_user_id, clean_target, status, join_type, now, now, last_error))
    row = cursor.fetchone()
    db.commit()
    cursor.close()
    return _row_to_dict(row) if row else {}


def mark_channel_joined(
    telegram_user_id: int,
    channel_target: Any,
    join_type: str = "auto_join",
) -> dict:
    """Mark a channel target (or its base parent chat) as successfully joined in the database."""
    info = extract_base_join_target(channel_target)
    clean_target = info["base_url"].strip().lower()
    now = datetime.now(timezone.utc)
    cursor = get_cursor()
    cursor.execute("""
        INSERT INTO channel_joins
            (telegram_user_id, channel_target, status, join_type, requested_at, last_attempt_at, joined_at, last_error)
        VALUES (%s, %s, 'joined', %s, %s, %s, %s, NULL)
        ON CONFLICT (telegram_user_id, channel_target) DO UPDATE
            SET status          = 'joined',
                join_type       = EXCLUDED.join_type,
                last_attempt_at = EXCLUDED.last_attempt_at,
                joined_at       = COALESCE(channel_joins.joined_at, EXCLUDED.joined_at),
                last_error      = NULL,
                updated_at      = NOW()
        RETURNING id, telegram_user_id, channel_target, status, join_type, requested_at, last_attempt_at, joined_at, retry_count, last_error;
    """, (telegram_user_id, clean_target, join_type, now, now, now))
    row = cursor.fetchone()
    db.commit()
    cursor.close()
    return _row_to_dict(row) if row else {}


def mark_channel_verified(
    telegram_user_id: int,
    channel_target: Any,
) -> dict:
    """Mark a channel target as verified (e.g. by @beem bot)."""
    info = extract_base_join_target(channel_target)
    clean_target = info["base_url"].strip().lower()
    now = datetime.now(timezone.utc)
    cursor = get_cursor()
    cursor.execute("""
        INSERT INTO channel_joins
            (telegram_user_id, channel_target, status, join_type, requested_at, last_attempt_at, joined_at, last_error)
        VALUES (%s, %s, 'verified', 'beem_verification', %s, %s, %s, NULL)
        ON CONFLICT (telegram_user_id, channel_target) DO UPDATE
            SET status          = 'verified',
                join_type       = 'beem_verification',
                last_attempt_at = EXCLUDED.last_attempt_at,
                joined_at       = COALESCE(channel_joins.joined_at, EXCLUDED.joined_at),
                last_error      = NULL,
                updated_at      = NOW()
        RETURNING id, telegram_user_id, channel_target, status, join_type, requested_at, last_attempt_at, joined_at, retry_count, last_error;
    """, (telegram_user_id, clean_target, now, now, now))
    row = cursor.fetchone()
    db.commit()
    cursor.close()
    return _row_to_dict(row) if row else {}


def get_pending_join_requests_older_than(
    telegram_user_id: int | None = None,
    seconds: int = 1000,
) -> list[dict]:
    """
    Fetch join requests that are still in 'pending' status and where
    last_attempt_at was more than `seconds` ago (default 1000s).
    """
    cursor = get_cursor()
    query = """
        SELECT id, telegram_user_id, channel_target, status, join_type,
               requested_at, last_attempt_at, joined_at, retry_count, last_error
        FROM channel_joins
        WHERE status = 'pending'
          AND last_attempt_at <= NOW() - (%s || ' seconds')::INTERVAL
    """
    params: list = [seconds]
    if telegram_user_id is not None:
        query += " AND telegram_user_id = %s"
        params.append(telegram_user_id)
    query += " ORDER BY last_attempt_at ASC;"

    cursor.execute(query, tuple(params))
    rows = cursor.fetchall()
    db.commit()
    cursor.close()
    return [_row_to_dict(r) for r in rows]


def is_channel_joined(telegram_user_id: int, channel_target: Any) -> bool:
    """
    Return True if channel or its parent group/channel is recorded as 'joined' or 'verified' in DB.
    Automatically resolves base target candidates so topic URLs match the parent chat.
    """
    info = extract_base_join_target(channel_target)
    candidates = [c.lower() for c in info.get("candidates", [])]
    if not candidates:
        candidates = [str(channel_target).strip().lower()]

    cursor = get_cursor()
    cursor.execute("""
        SELECT 1 FROM channel_joins
        WHERE telegram_user_id = %s
          AND LOWER(channel_target) = ANY(%s)
          AND status IN ('joined', 'verified')
        LIMIT 1;
    """, (telegram_user_id, candidates))
    found = cursor.fetchone() is not None
    db.commit()
    cursor.close()
    return found


def get_join_status(telegram_user_id: int, channel_target: Any) -> dict | None:
    """Get the current join record for a user + target (checking base target candidates)."""
    info = extract_base_join_target(channel_target)
    candidates = [c.lower() for c in info.get("candidates", [])]
    if not candidates:
        candidates = [str(channel_target).strip().lower()]

    cursor = get_cursor()
    cursor.execute("""
        SELECT id, telegram_user_id, channel_target, status, join_type,
               requested_at, last_attempt_at, joined_at, retry_count, last_error
        FROM channel_joins
        WHERE telegram_user_id = %s AND LOWER(channel_target) = ANY(%s)
        ORDER BY CASE WHEN status IN ('joined', 'verified') THEN 1 ELSE 2 END, id DESC
        LIMIT 1;
    """, (telegram_user_id, candidates))
    row = cursor.fetchone()
    db.commit()
    cursor.close()
    return _row_to_dict(row) if row else None


def _row_to_dict(row: tuple) -> dict:
    return {
        "id": row[0],
        "telegram_user_id": row[1],
        "channel_target": row[2],
        "status": row[3],
        "join_type": row[4],
        "requested_at": row[5],
        "last_attempt_at": row[6],
        "joined_at": row[7],
        "retry_count": row[8],
        "last_error": row[9],
    }
