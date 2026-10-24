# ─────────────────────────────────────────────────────────────────────────────
#  models / users.py
#
#  CRUD helpers for telegram_users table.
#
#  Schema (current)
#  ───────────────
#    id                  BIGSERIAL    PRIMARY KEY
#    user_id             BIGINT       NOT NULL    (Telegram user who added this account)
#    api_id              INTEGER      NOT NULL
#    api_hash            TEXT         NOT NULL
#    phone_number        VARCHAR(20)  NOT NULL UNIQUE
#    session_string      TEXT         (Encrypted via Fernet)
#    topic_id            VARCHAR(100) (url_key string referencing topics.url_key)
#    plan_id             BIGINT       FK → plans(id)
#    plan_started_at     TIMESTAMPTZ
#    plan_expired_at     TIMESTAMPTZ
#    forwarding_enabled  BOOLEAN      DEFAULT TRUE
#    created_at          TIMESTAMPTZ  DEFAULT NOW()
#    updated_at          TIMESTAMPTZ  DEFAULT NOW()
#
#  NOTE: source_url, forwarding_delay, forwarding_delay_max are REMOVED.
#        Source URL is stored in topics.source_url (per url_key group).
# ─────────────────────────────────────────────────────────────────────────────

try:
    from config.db import db, get_cursor
    from config.logger import LOG
    from utils.encryption import encrypt_session, decrypt_session
except ImportError:
    from userbot.config.db import db, get_cursor
    from userbot.config.logger import LOG
    from userbot.utils.encryption import encrypt_session, decrypt_session


def bootstrap_users_table() -> None:
    """Ensure telegram_users table and all columns exist."""
    cursor = get_cursor()

    # Base table create
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS telegram_users (
            id                   BIGSERIAL    PRIMARY KEY,
            user_id              BIGINT       NOT NULL DEFAULT 0,
            api_id               INTEGER      NOT NULL,
            api_hash             TEXT         NOT NULL,
            phone_number         VARCHAR(20)  NOT NULL,
            session_string       TEXT         NOT NULL,
            topic_id             VARCHAR(100),
            channel_id           VARCHAR(100),
            plan_id              BIGINT       REFERENCES plans(id) ON DELETE SET NULL,
            plan_started_at      TIMESTAMPTZ,
            plan_expired_at      TIMESTAMPTZ,
            forwarding_enabled   BOOLEAN      NOT NULL DEFAULT TRUE,
            created_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            updated_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW()
        );
    """)

    # Column migrations for existing installs
    cursor.execute("""
        ALTER TABLE telegram_users
            ADD COLUMN IF NOT EXISTS user_id              BIGINT       NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS phone_number         VARCHAR(20),
            ADD COLUMN IF NOT EXISTS topic_id             VARCHAR(100),
            ADD COLUMN IF NOT EXISTS channel_id           VARCHAR(100),
            ADD COLUMN IF NOT EXISTS plan_id              BIGINT       REFERENCES plans(id) ON DELETE SET NULL,
            ADD COLUMN IF NOT EXISTS plan_started_at      TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS plan_expired_at      TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS forwarding_enabled   BOOLEAN      NOT NULL DEFAULT TRUE,
            ADD COLUMN IF NOT EXISTS session_status       VARCHAR(30)  NOT NULL DEFAULT 'active',
            ADD COLUMN IF NOT EXISTS session_error        TEXT,
            ADD COLUMN IF NOT EXISTS last_session_check_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS updated_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW();
        ALTER TABLE telegram_users
            ALTER COLUMN channel_id TYPE VARCHAR(100) USING channel_id::VARCHAR;
        ALTER TABLE telegram_users
            ALTER COLUMN plan_started_at DROP NOT NULL;
    """)

    # Rename/fix old phone column if it exists
    cursor.execute("""
        DO $$ BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='telegram_users' AND column_name='phone'
            ) THEN
                ALTER TABLE telegram_users ALTER COLUMN phone DROP NOT NULL;
                ALTER TABLE telegram_users DROP CONSTRAINT IF EXISTS telegram_users_phone_key;
                UPDATE telegram_users SET phone_number = phone WHERE phone_number IS NULL AND phone IS NOT NULL;
                UPDATE telegram_users SET phone = phone_number WHERE phone IS NULL AND phone_number IS NOT NULL;
            END IF;
        END $$;
    """)

    # Rename is_forward → forwarding_enabled (migrate data)
    cursor.execute("""
        DO $$ BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='telegram_users' AND column_name='is_forward'
            ) THEN
                UPDATE telegram_users SET forwarding_enabled = is_forward WHERE forwarding_enabled IS DISTINCT FROM is_forward;
            END IF;
        END $$;
    """)

    # Rename plan_st → plan_started_at (migrate data)
    cursor.execute("""
        DO $$ BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='telegram_users' AND column_name='plan_st'
            ) THEN
                UPDATE telegram_users SET plan_started_at = plan_st WHERE plan_started_at IS DISTINCT FROM plan_st;
            END IF;
        END $$;
    """)

    # migrate added_by → user_id and make added_by nullable with default 0
    cursor.execute("""
        DO $$ BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='telegram_users' AND column_name='added_by'
            ) THEN
                ALTER TABLE telegram_users ALTER COLUMN added_by DROP NOT NULL;
                ALTER TABLE telegram_users ALTER COLUMN added_by SET DEFAULT 0;
                UPDATE telegram_users SET user_id = added_by WHERE (user_id IS NULL OR user_id = 0) AND added_by IS NOT NULL;
                UPDATE telegram_users SET added_by = user_id WHERE added_by IS NULL OR added_by = 0;
            END IF;
        END $$;
    """)

    # Unique index on phone_number (if not already on phone)
    cursor.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_telegram_users_phone_number
        ON telegram_users(phone_number);
    """)

    db.commit()
    cursor.close()
    LOG.info("telegram_users table & columns ready")


# ─────────────────────────────────────────────
#  Internal helpers
# ─────────────────────────────────────────────
def _compute_expired_at(plan_id: int | None, started_at_expr: str = "NOW()") -> str:
    """Returns a SQL expression for plan_expired_at based on plan validity."""
    if plan_id is None:
        return "NOW() + INTERVAL '7 days'"  # default trial
    return f"(SELECT {started_at_expr} + (validity_days || ' days')::INTERVAL FROM plans WHERE id = {plan_id})"


def _row_to_dict(row: tuple) -> dict:
    """Convert DB tuple -> dict for telegram_users."""
    return {
        "id": row[0],
        "user_id": row[1],
        "api_id": row[2],
        "api_hash": row[3],
        "phone_number": row[4],
        "session_string": decrypt_session(row[5]) if row[5] else "",
        "topic_id": row[6],
        "topic_name": row[7] or row[6],
        "plan_id": row[8],
        "plan_name": row[9],
        "validity_days": row[10],
        "plan_started_at": row[11],
        "plan_expired_at": row[12],
        "forwarding_enabled": row[13],
        "created_at": row[14],
        "updated_at": row[15],
        "channel_id": row[16] if len(row) > 16 else None,
        "session_status": row[17] if len(row) > 17 and row[17] else "active",
        "session_error": row[18] if len(row) > 18 else None,
        "last_session_check_at": row[19] if len(row) > 19 else None,
        "validate": row[17] if len(row) > 17 and row[17] else "active",
        # Legacy aliases
        "phone": row[4],
        "is_forward": row[13],
        "plan_st": row[11],
        "api_hash_raw": row[3],
    }


_SELECT = """
    SELECT u.id, u.user_id, u.api_id, u.api_hash, u.phone_number, u.session_string,
           u.topic_id, t.name, u.plan_id, p.name,
           p.validity_days, u.plan_started_at, u.plan_expired_at,
           u.forwarding_enabled, u.created_at, u.updated_at, u.channel_id,
           COALESCE(u.session_status, 'active'), u.session_error, u.last_session_check_at
    FROM telegram_users u
    LEFT JOIN (
        SELECT DISTINCT ON (url_key) url_key, name FROM topics ORDER BY url_key
    ) t ON t.url_key = u.topic_id
    LEFT JOIN plans  p ON p.id = u.plan_id
"""


# ─────────────────────────────────────────────
#  CREATE / UPDATE
# ─────────────────────────────────────────────
def normalize_channel_id(val: str | int | None) -> str | None:
    if not val:
        return None
    s = str(val).strip()
    if not s or s == "0":
        return None
    if "t.me/" in s:
        part = s.split("t.me/")[1].split("/")[0].strip()
        if part.startswith("c/"):
            cid = part.replace("c/", "")
            return f"-100{cid}" if not cid.startswith("-100") else cid
        elif part.startswith("+"):
            return None
        else:
            return f"@{part}" if not part.startswith("@") else part
    if s.isdigit() and len(s) >= 9 and not s.startswith("-100"):
        return f"-100{s}"
    return s


def save_telegram_user(
    added_by: int,
    api_id: int,
    api_hash: str,
    phone: str,
    session_string: str,
    topic_id: str | None = None,
    plan_id: int | None = None,
    is_forward: bool = True,
    channel_id: int | str | None = None,
    **kwargs,
) -> None:
    """Insert or update a linked Telegram account. Encrypts session string."""
    enc_session = encrypt_session(session_string)
    ch_id = normalize_channel_id(channel_id)
    cursor = get_cursor()
    cursor.execute(
        """
        INSERT INTO telegram_users
            (user_id, added_by, api_id, api_hash, phone, phone_number, session_string,
             topic_id, plan_id, plan_started_at, plan_expired_at, forwarding_enabled, channel_id)
        VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s,
            CASE WHEN %s::BIGINT IS NULL THEN NULL ELSE NOW() END,
            CASE WHEN %s::BIGINT IS NULL THEN NULL ELSE NOW() + ((SELECT validity_days FROM plans WHERE id = %s::BIGINT) || ' days')::INTERVAL END,
            %s, %s
        )
        ON CONFLICT (phone_number) DO UPDATE
            SET api_id               = EXCLUDED.api_id,
                api_hash             = EXCLUDED.api_hash,
                session_string       = EXCLUDED.session_string,
                phone                = EXCLUDED.phone_number,
                phone_number         = EXCLUDED.phone_number,
                user_id              = EXCLUDED.user_id,
                added_by             = EXCLUDED.added_by,
                topic_id             = EXCLUDED.topic_id,
                plan_id              = EXCLUDED.plan_id,
                plan_started_at      = CASE WHEN EXCLUDED.plan_id IS NULL THEN NULL ELSE COALESCE(telegram_users.plan_started_at, NOW()) END,
                plan_expired_at      = CASE WHEN EXCLUDED.plan_id IS NULL THEN NULL ELSE NOW() + ((SELECT validity_days FROM plans WHERE id = EXCLUDED.plan_id) || ' days')::INTERVAL END,
                forwarding_enabled   = COALESCE(telegram_users.forwarding_enabled, EXCLUDED.forwarding_enabled),
                channel_id           = COALESCE(EXCLUDED.channel_id, telegram_users.channel_id),
                updated_at           = NOW();
    """,
        (
            added_by,
            added_by,
            api_id,
            api_hash,
            phone,
            phone,
            enc_session,
            topic_id,
            plan_id,
            plan_id,
            plan_id,
            plan_id,
            is_forward,
            ch_id,
        ),
    )
    db.commit()
    cursor.close()
    LOG.info("Saved telegram user phone=%s topic=%s plan=%s channel_id=%s", phone, topic_id, plan_id, ch_id)


# ─────────────────────────────────────────────
#  READ
# ─────────────────────────────────────────────
def get_all_users() -> list[dict]:
    cursor = get_cursor()
    cursor.execute(_SELECT + " ORDER BY u.created_at DESC;")
    rows = cursor.fetchall()
    try:
        db.commit()
    except Exception:
        pass
    cursor.close()
    return [_row_to_dict(r) for r in rows]


def get_active_users() -> list[dict]:
    """Return users whose plan has not expired and forwarding is enabled with a valid session string."""
    cursor = get_cursor()
    cursor.execute(_SELECT + """
        WHERE u.forwarding_enabled = TRUE
          AND (u.plan_expired_at IS NULL OR u.plan_expired_at > NOW())
          AND u.session_string IS NOT NULL
          AND length(u.session_string) > 50
        ORDER BY u.id;
        """)
    rows = cursor.fetchall()
    try:
        db.commit()
    except Exception:
        pass
    cursor.close()
    return [_row_to_dict(r) for r in rows]


def get_user_by_id(user_id: int) -> dict | None:
    cursor = get_cursor()
    cursor.execute(_SELECT + " WHERE u.id = %s;", (user_id,))
    row = cursor.fetchone()
    try:
        db.commit()
    except Exception:
        pass
    cursor.close()
    return _row_to_dict(row) if row else None


# ─────────────────────────────────────────────
#  UPDATE helpers
# ─────────────────────────────────────────────
def update_user_forwarding(user_id: int, enabled: bool) -> dict | None:
    cursor = get_cursor()
    cursor.execute(
        "UPDATE telegram_users SET forwarding_enabled=%s, updated_at=NOW() WHERE id=%s;",
        (enabled, user_id),
    )
    db.commit()
    cursor.close()
    LOG.info("forwarding_enabled=%s for user_id=%s", enabled, user_id)
    return get_user_by_id(user_id)


def update_user_session_status(
    user_id: int,
    status: str,
    error: str | None = None,
) -> dict | None:
    """
    Update session status in DB (active, banned, expired, suspended).
    Forwarding is NOT modified automatically; it is controlled exclusively via manual toggle.
    """
    cursor = get_cursor()
    cursor.execute(
        """
        UPDATE telegram_users
        SET session_status = %s,
            session_error = %s,
            last_session_check_at = NOW(),
            updated_at = NOW()
        WHERE id = %s;
        """,
        (status, error, user_id),
    )
    db.commit()
    cursor.close()
    LOG.info("Updated session_status=%s for user_id=%s", status, user_id)
    return get_user_by_id(user_id)



def update_user_topic(user_id: int, topic_id: str | int | None) -> dict | None:
    """Update the topic assigned to a user. topic_id should be a url_key string."""
    # Accept either a url_key string or a numeric id (legacy); store as VARCHAR
    topic_val = str(topic_id) if topic_id is not None else None
    cursor = get_cursor()
    cursor.execute(
        "UPDATE telegram_users SET topic_id=%s, updated_at=NOW() WHERE id=%s;",
        (topic_val, user_id),
    )
    db.commit()
    cursor.close()
    LOG.info("topic_id=%s for user_id=%s", topic_val, user_id)
    return get_user_by_id(user_id)


def upgrade_user_plan(user_id: int, plan_id: int | None) -> dict | None:
    """Upgrade: switch plan_id but keep original plan_started_at."""
    cursor = get_cursor()
    if plan_id is None:
        cursor.execute(
            "UPDATE telegram_users SET plan_id = NULL, plan_expired_at = NULL, updated_at = NOW() WHERE id = %s;",
            (user_id,),
        )
    else:
        cursor.execute(
            """
            UPDATE telegram_users
            SET plan_id = %s,
                plan_expired_at = plan_started_at + (
                    COALESCE((SELECT validity_days FROM plans WHERE id = %s), 30) || ' days'
                )::INTERVAL,
                updated_at = NOW()
            WHERE id = %s;
        """,
            (plan_id, plan_id, user_id),
        )
    db.commit()
    cursor.close()
    LOG.info("Upgraded plan_id=%s for user_id=%s", plan_id, user_id)
    return get_user_by_id(user_id)


def update_user_plan(user_id: int, plan_id: int | None) -> dict | None:
    """Update: switch plan_id AND reset plan_started_at to NOW()."""
    cursor = get_cursor()
    if plan_id is None:
        cursor.execute(
            "UPDATE telegram_users SET plan_id = NULL, plan_started_at = NOW(), plan_expired_at = NULL, updated_at = NOW() WHERE id = %s;",
            (user_id,),
        )
    else:
        cursor.execute(
            """
            UPDATE telegram_users
            SET plan_id = %s,
                plan_started_at = NOW(),
                plan_expired_at = NOW() + (
                    COALESCE((SELECT validity_days FROM plans WHERE id = %s), 30) || ' days'
                )::INTERVAL,
                updated_at = NOW()
            WHERE id = %s;
        """,
            (plan_id, plan_id, user_id),
        )
    db.commit()
    cursor.close()
    LOG.info("Updated plan_id=%s for user_id=%s", plan_id, user_id)
    return get_user_by_id(user_id)


def get_banned_users() -> list[dict]:
    """Return users marked as banned in forwarding_state."""
    cursor = get_cursor()
    cursor.execute(_SELECT + """
        WHERE u.id IN (
            SELECT telegram_user_id FROM forwarding_state WHERE status = 'banned'
        )
        ORDER BY u.created_at DESC;
    """)
    rows = cursor.fetchall()
    cursor.close()
    return [_row_to_dict(r) for r in rows]


def update_session_string(user_id: int, session_string: str) -> dict | None:
    """Update encrypted session string for a user (re-auth)."""
    enc = encrypt_session(session_string)
    cursor = get_cursor()
    cursor.execute(
        "UPDATE telegram_users SET session_string=%s, updated_at=NOW() WHERE id=%s;",
        (enc, user_id),
    )
    db.commit()
    cursor.close()
    LOG.info("Updated session_string for user_id=%s", user_id)
    return get_user_by_id(user_id)


def update_user_channel_id(user_id: int, channel_id: int | str | None) -> dict | None:
    ch_id = normalize_channel_id(channel_id)
    cursor = get_cursor()
    cursor.execute(
        "UPDATE telegram_users SET channel_id=%s, updated_at=NOW() WHERE id=%s;",
        (ch_id, user_id),
    )
    db.commit()
    cursor.close()
    LOG.info("channel_id=%s for user_id=%s", ch_id, user_id)
    return get_user_by_id(user_id)


def delete_telegram_user(user_id: int) -> bool:
    cursor = get_cursor()
    cursor.execute("DELETE FROM telegram_users WHERE id=%s RETURNING id;", (user_id,))
    deleted = cursor.fetchone() is not None
    db.commit()
    cursor.close()
    if deleted:
        LOG.info("Deleted telegram_user id=%s", user_id)
    return deleted

