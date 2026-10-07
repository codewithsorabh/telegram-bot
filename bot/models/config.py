# ─────────────────────────────────────────────────────────────────────────────
#  models / config.py
#
#  PostgreSQL table: config
#
#  Schema
#  ──────
#    id          SERIAL PRIMARY KEY
#    admin_id    BIGINT NOT NULL UNIQUE
#    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
#    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
# ─────────────────────────────────────────────────────────────────────────────

import os
from dotenv import load_dotenv
from config.db import db, get_cursor
from config.logger import LOG

load_dotenv()


def get_primary_admins() -> set[int]:
    """Return all primary admin IDs from ADMIN_ID env var (comma-separated)."""
    val = os.getenv("ADMIN_ID", "")
    ids: set[int] = set()
    for part in val.split(","):
        part = part.strip()
        if part.isdigit():
            ids.add(int(part))
    return ids


def get_primary_admin() -> int | None:
    """Return the first primary admin_id (backwards-compat helper)."""
    ids = get_primary_admins()
    return next(iter(ids)) if ids else None


def is_primary_admin(user_id: int | str) -> bool:
    """Return True if user_id is any of the primary admins from .env."""
    try:
        uid = int(user_id)
    except (ValueError, TypeError):
        return False
    return uid in get_primary_admins()


def is_secondary_admin(user_id: int | str) -> bool:
    """Return True if user_id is in secondary admin config table (excluding primary)."""
    try:
        uid = int(user_id)
    except (ValueError, TypeError):
        return False
    if is_primary_admin(uid):
        return False
    cursor = get_cursor()
    cursor.execute("SELECT 1 FROM config WHERE admin_id = %s;", (uid,))
    found = cursor.fetchone() is not None
    cursor.close()
    return found


def is_authorized_admin(user_id: int | str) -> bool:
    """Return True if user_id is primary OR secondary admin."""
    return is_primary_admin(user_id) or is_secondary_admin(user_id)


def bootstrap_config_table() -> None:
    """Create the `config` table for secondary admins and settings."""
    cursor = get_cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS config (
            id          SERIAL       PRIMARY KEY,
            admin_id    BIGINT       NOT NULL UNIQUE,
            admin_name  TEXT,
            permissions JSONB        NOT NULL DEFAULT '{"manage_users": true, "manage_topics": true}'::jsonb,
            created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
        );
    """)

    cursor.execute("""
        ALTER TABLE config
        ADD COLUMN IF NOT EXISTS permissions JSONB NOT NULL DEFAULT '{"manage_users": true, "manage_topics": true}'::jsonb;
    """)

    cursor.execute("""
        ALTER TABLE config
        ADD COLUMN IF NOT EXISTS admin_name TEXT;
    """)

    # Auto-update trigger for updated_at
    cursor.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_trigger
                WHERE tgname = 'trg_config_updated_at'
            ) THEN
                CREATE TRIGGER trg_config_updated_at
                BEFORE UPDATE ON config
                FOR EACH ROW EXECUTE FUNCTION _set_updated_at();
            END IF;
        END $$;
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS system_settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """)

    # Pre-seed all primary admins from .env into config table
    for primary in get_primary_admins():
        cursor.execute(
            """
            INSERT INTO config (admin_id, admin_name)
            VALUES (%s, 'Primary Admin')
            ON CONFLICT (admin_id) DO NOTHING;
        """,
            (primary,),
        )

    db.commit()
    cursor.close()
    LOG.info("config & system_settings tables ready")


# ─────────────────────────────────────────────
#  System Settings Helpers
# ─────────────────────────────────────────────
def get_setting(key: str, default: str = "true") -> str:
    """Get system setting value from DB (or fallback to .env / default)."""
    cursor = get_cursor()
    cursor.execute("SELECT value FROM system_settings WHERE key = %s;", (key,))
    row = cursor.fetchone()
    cursor.close()
    if row and row[0] is not None:
        return row[0]
    return os.getenv(key, default)


def set_setting(key: str, value: str) -> None:
    """Set system setting key-value in DB."""
    cursor = get_cursor()
    cursor.execute(
        """
        INSERT INTO system_settings (key, value) VALUES (%s, %s)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;
    """,
        (key, str(value)),
    )
    db.commit()
    cursor.close()


def toggle_setting(key: str, default: str = "true") -> bool:
    """Toggle a boolean setting between 'true' and 'false'."""
    cur_val = get_setting(key, default).strip().lower() in ("true", "1", "yes", "on")
    new_val = "false" if cur_val else "true"
    set_setting(key, new_val)
    return new_val == "true"


def is_topic_step_enabled() -> bool:
    """Return True if SELECT_STEP_TOPIC step is enabled."""
    return get_setting("SELECT_STEP_TOPIC", "true").strip().lower() in (
        "true",
        "1",
        "yes",
        "on",
    )


def is_plan_step_enabled() -> bool:
    """Return True if SELECT_STEP_PLAN step is enabled."""
    return get_setting("SELECT_STEP_PLAN", "true").strip().lower() in (
        "true",
        "1",
        "yes",
        "on",
    )


def get_page_size() -> int:
    """Return pagination page_size setting from DB (default: 10, min: 10, max: 70)."""
    val = get_setting("PAGE_SIZE", "10")
    try:
        size = int(val)
        return max(10, min(size, 70))
    except (ValueError, TypeError):
        return 10


def set_page_size(size: int) -> None:
    """Save pagination page_size in DB (clamped between 10 and 70)."""
    clamped = max(10, min(size, 70))
    set_setting("PAGE_SIZE", str(clamped))


def cycle_page_size() -> int:
    """Cycle page size through [5, 10, 15, 20] and return the new value."""
    sizes = [5, 10, 15, 20]
    cur = get_page_size()
    if cur in sizes:
        idx = (sizes.index(cur) + 1) % len(sizes)
        next_size = sizes[idx]
    else:
        next_size = 5
    set_page_size(next_size)
    return next_size


def get_owner_log_channel() -> str | None:
    """Return Owner Log Channel ID string or None."""
    val = get_setting("OWNER_LOG_CHANNEL", "")
    return val if val and val != "0" else None


def set_owner_log_channel(channel_id: str | int | None) -> None:
    """Save Owner Log Channel ID."""
    set_setting("OWNER_LOG_CHANNEL", str(channel_id) if channel_id else "")


def add_secondary_admin(admin_id: int, admin_name: str = "") -> bool:
    """Add or update a secondary admin with optional name."""
    cursor = get_cursor()
    try:
        cursor.execute(
            """
            INSERT INTO config (admin_id, admin_name, permissions)
            VALUES (%s, %s, '{"manage_users": true, "manage_topics": true}'::jsonb)
            ON CONFLICT (admin_id) DO UPDATE
                SET admin_name = EXCLUDED.admin_name,
                    updated_at = NOW();
        """,
            (admin_id, admin_name.strip()),
        )
        db.commit()
        cursor.close()
        LOG.info("Added secondary admin_id=%s (name='%s')", admin_id, admin_name)
        return True
    except Exception as exc:
        db.rollback()
        cursor.close()
        LOG.error("Failed to add secondary admin_id=%s: %s", admin_id, exc)
        return False


def get_admin_permissions(admin_id: int | str) -> dict:
    """Return permissions dict for an admin."""
    try:
        aid = int(admin_id)
    except (ValueError, TypeError):
        return {"manage_users": False, "manage_topics": False}
    if is_primary_admin(aid):
        return {"manage_users": True, "manage_topics": True}
    cursor = get_cursor()
    cursor.execute("SELECT permissions FROM config WHERE admin_id = %s;", (aid,))
    row = cursor.fetchone()
    cursor.close()
    if row and row[0]:
        perms = row[0]
        if isinstance(perms, str):
            import json

            perms = json.loads(perms)
        return perms
    return {"manage_users": True, "manage_topics": True}


def toggle_admin_permission(admin_id: int, perm_name: str) -> dict:
    """Toggle a permission key (manage_users, manage_topics) for secondary admin."""
    perms = get_admin_permissions(admin_id)
    perms[perm_name] = not perms.get(perm_name, True)

    import json

    cursor = get_cursor()
    cursor.execute(
        "UPDATE config SET permissions = %s WHERE admin_id = %s RETURNING permissions;",
        (json.dumps(perms), admin_id),
    )
    db.commit()
    cursor.close()
    return perms


def remove_secondary_admin(admin_id: int) -> bool:
    """Remove a secondary admin ID (cannot remove primary admin)."""
    if is_primary_admin(admin_id):
        LOG.warning("Cannot remove primary admin_id=%s from .env", admin_id)
        return False

    cursor = get_cursor()
    cursor.execute("DELETE FROM config WHERE admin_id = %s RETURNING id;", (admin_id,))
    deleted = cursor.fetchone() is not None
    db.commit()
    cursor.close()
    if deleted:
        LOG.info("Removed secondary admin_id=%s", admin_id)
    return deleted


def get_all_admins() -> list[dict]:
    """Return all admin IDs with primary flag, admin_name, and permissions."""
    primary_ids = get_primary_admins()
    cursor = get_cursor()
    cursor.execute(
        "SELECT id, admin_id, permissions, admin_name, created_at FROM config ORDER BY created_at ASC;"
    )
    rows = cursor.fetchall()
    cursor.close()

    result = []
    seen: set[int] = set()

    # Add primary admins at the top (from env, not DB rows)
    for primary in sorted(primary_ids):
        if primary not in seen:
            result.append(
                {
                    "id": 0,
                    "admin_id": primary,
                    "admin_name": "Primary Admin",
                    "is_primary": True,
                    "permissions": {
                        "manage_users": False,
                        "manage_topics": False,
                    },
                    "created_at": None,
                }
            )
            seen.add(primary)

    for r in rows:
        aid = r[1]
        perms = (
            r[2]
            if r[2]
            else {"manage_users": True, "manage_topics": True, "manage_plans": True}
        )
        name = r[3] if r[3] else f"{aid}"
        if isinstance(perms, str):
            import json

            perms = json.loads(perms)
        if aid not in seen:
            result.append(
                {
                    "id": r[0],
                    "admin_id": aid,
                    "admin_name": name,
                    "is_primary": aid in primary_ids,
                    "permissions": perms,
                    "created_at": r[4],
                }
            )
            seen.add(aid)

    return result


# Legacy alias
is_admin = is_authorized_admin


async def check_user_permission(
    update, perm_name: str | None = None, is_settings: bool = False
) -> bool:
    """
    Role Authorization Check with Granular Permissions.
    """
    from templates.messages import SETTINGS_UNAUTHORIZED_MSG, UNAUTHORIZED_MSG

    user = update.effective_user
    if not user:
        return False

    uid = user.id
    query = update.callback_query

    # Primary admin check
    if is_primary_admin(uid):
        return True

    # Secondary admin check
    if is_secondary_admin(uid):
        if is_settings:
            if query:
                await query.answer(
                    "⛔ Only Primary Admin can access Settings.", show_alert=True
                )
            elif update.message:
                await update.message.reply_html(SETTINGS_UNAUTHORIZED_MSG)
            return False

        if perm_name:
            perms = get_admin_permissions(uid)
            if not perms.get(perm_name, True):
                if query:
                    await query.answer(
                        f"⛔ You lack '{perm_name}' permission.", show_alert=True
                    )
                elif update.message:
                    await update.message.reply_html(
                        f"⛔ <b>Permission Denied:</b> Missing <code>{perm_name}</code>."
                    )
                return False

        return True

    # Unauthorized user
    if query:
        await query.answer("⛔ Unauthorized Access.", show_alert=True)
        try:
            await query.edit_message_text(UNAUTHORIZED_MSG, parse_mode="HTML")
        except Exception:
            pass
    elif update.message:
        await update.message.reply_html(UNAUTHORIZED_MSG)

    return False
