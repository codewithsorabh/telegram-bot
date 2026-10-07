# ─────────────────────────────────────────────
#  config / migrations.py
#
#  Automated schema migration engine (Prisma-like)
#  Handles zero-downtime, non-destructive schema
#  upgrades for PostgreSQL tables.
# ─────────────────────────────────────────────

import json
from config.db import db, get_cursor
from config.logger import LOG


def run_auto_migrations() -> None:
    """
    Execute all pending database schema migrations.
    Safely adds columns, constraints, indices, and transforms data.
    Uses pg_advisory_lock to prevent deadlocks when bot and userbot start concurrently.
    """
    cursor = get_cursor()

    try:
        cursor.execute("SELECT pg_advisory_lock(84729103);")
        # ─────────────────────────────────────────
        # 1. Base Topics Table Structure
        # ─────────────────────────────────────────
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS topics (
                id               BIGSERIAL    PRIMARY KEY,
                url_key          VARCHAR(100) NOT NULL,
                name             VARCHAR(150) NOT NULL,
                url              TEXT         NOT NULL,
                enabled          BOOLEAN      NOT NULL DEFAULT TRUE,
                interval_seconds INTEGER      NOT NULL DEFAULT 60,
                priority         INTEGER      NOT NULL DEFAULT 0,
                created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
                updated_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW()
            );
        """)

        # Add missing columns to existing topics table if needed
        cursor.execute("""
            ALTER TABLE topics
                ADD COLUMN IF NOT EXISTS url_key          VARCHAR(100),
                ADD COLUMN IF NOT EXISTS name             VARCHAR(150),
                ADD COLUMN IF NOT EXISTS url              TEXT,
                ADD COLUMN IF NOT EXISTS enabled          BOOLEAN      NOT NULL DEFAULT TRUE,
                ADD COLUMN IF NOT EXISTS interval_seconds INTEGER      NOT NULL DEFAULT 60,
                ADD COLUMN IF NOT EXISTS priority         INTEGER      NOT NULL DEFAULT 0,
                ADD COLUMN IF NOT EXISTS updated_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW();
        """)

        # ─────────────────────────────────────────
        # 2. Data Migration from legacy topic_urls JSONB (if legacy column exists)
        # ─────────────────────────────────────────
        cursor.execute("""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'topics' AND column_name = 'topic_name'
            );
        """)
        has_legacy_col = cursor.fetchone()[0]

        if has_legacy_col:
            cursor.execute("""
                SELECT id, topic_name, topic_urls FROM topics WHERE url IS NULL OR url_key IS NULL;
            """)
            legacy_rows = cursor.fetchall()
            for r in legacy_rows:
                tid, topic_name, topic_urls = r[0], r[1], r[2]
                base_key = (topic_name or f"topic_{tid}").lower().replace(" ", "_")

                urls_list = topic_urls if isinstance(topic_urls, list) else []
                if isinstance(topic_urls, str):
                    try:
                        urls_list = json.loads(topic_urls)
                    except Exception:
                        urls_list = []

                if not urls_list:
                    cursor.execute("""
                        UPDATE topics
                        SET url_key = %s, name = %s, url = 'https://t.me/placeholder'
                        WHERE id = %s;
                    """, (base_key, topic_name or f"Topic #{tid}", tid))
                else:
                    for idx, uentry in enumerate(urls_list):
                        target_url = uentry.get("url") or uentry.get("normalized") or uentry.get("raw") or "" if isinstance(uentry, dict) else str(uentry)
                        if not target_url:
                            continue
                        if idx == 0:
                            cursor.execute("""
                                UPDATE topics
                                SET url_key = %s, name = %s, url = %s
                                WHERE id = %s;
                            """, (base_key, topic_name or f"Topic #{tid}", target_url, tid))
                        else:
                            cursor.execute("""
                                INSERT INTO topics (url_key, name, url, enabled, interval_seconds, priority)
                                VALUES (%s, %s, %s, true, 60, 0)
                                ON CONFLICT DO NOTHING;
                            """, (base_key, topic_name or f"Topic #{tid}", target_url))

        # Drop legacy columns if present
        cursor.execute("""
            ALTER TABLE topics
                DROP COLUMN IF EXISTS topic_name,
                DROP COLUMN IF EXISTS topic_urls;
        """)

        # Fill any remaining NULLs
        cursor.execute("""
            UPDATE topics SET url_key = 'default_' || id WHERE url_key IS NULL;
            UPDATE topics SET name = 'Topic #' || id WHERE name IS NULL;
            UPDATE topics SET url = 'https://t.me/placeholder' WHERE url IS NULL;
        """)

        # Set NOT NULL constraints
        cursor.execute("""
            ALTER TABLE topics ALTER COLUMN url_key SET NOT NULL;
            ALTER TABLE topics ALTER COLUMN name SET NOT NULL;
            ALTER TABLE topics ALTER COLUMN url SET NOT NULL;
        """)

        # Add composite unique constraint UNIQUE(url_key, url)
        cursor.execute("""
            DO $$ BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint WHERE conname = 'uq_topics_url_key_url'
                ) THEN
                    ALTER TABLE topics ADD CONSTRAINT uq_topics_url_key_url UNIQUE (url_key, url);
                END IF;
            END $$;
        """)

        # Add index on url_key
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_topics_url_key ON topics(url_key);
        """)

        # ─────────────────────────────────────────
        # 3. Migrate telegram_users table
        # ─────────────────────────────────────────
        # Add topic_id column if missing
        cursor.execute("""
            ALTER TABLE telegram_users
                ADD COLUMN IF NOT EXISTS topic_id VARCHAR(100);
        """)

        # Drop old FK constraint on topic_id if exists
        cursor.execute("""
            DO $$ BEGIN
                IF EXISTS (
                    SELECT 1 FROM pg_constraint WHERE conname = 'telegram_users_topic_id_fkey'
                ) THEN
                    ALTER TABLE telegram_users DROP CONSTRAINT telegram_users_topic_id_fkey;
                END IF;
            END $$;
        """)

        # Alter topic_id type to VARCHAR(100)
        cursor.execute("""
            ALTER TABLE telegram_users ALTER COLUMN topic_id TYPE VARCHAR(100) USING topic_id::VARCHAR;
        """)

        # Migrate legacy topic_key or group_id if present, convert numeric topic_id → url_key
        cursor.execute("""
            DO $$ BEGIN
                IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='telegram_users' AND column_name='topic_key') THEN
                    UPDATE telegram_users SET topic_id = topic_key WHERE topic_id IS NULL AND topic_key IS NOT NULL;
                END IF;
                IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='telegram_users' AND column_name='group_id') THEN
                    UPDATE telegram_users SET topic_id = group_id::VARCHAR WHERE topic_id IS NULL AND group_id IS NOT NULL;
                END IF;
                UPDATE telegram_users u
                SET topic_id = t.url_key
                FROM topics t
                WHERE u.topic_id = t.id::VARCHAR AND u.topic_id ~ '^\d+$';
            END $$;
        """)

        # Drop removed columns
        cursor.execute("""
            ALTER TABLE telegram_users
                DROP COLUMN IF EXISTS source_url,
                DROP COLUMN IF EXISTS forwarding_delay,
                DROP COLUMN IF EXISTS forwarding_delay_max;
        """)

        # Drop obsolete FK constraints if present
        cursor.execute("""
            DO $$ BEGIN
                IF EXISTS (
                    SELECT 1 FROM pg_constraint WHERE conname = 'fk_telegram_users_topics_url_key'
                ) THEN
                    ALTER TABLE telegram_users DROP CONSTRAINT fk_telegram_users_topics_url_key;
                END IF;
            END $$;
        """)

        # Index on telegram_users(topic_id)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_telegram_users_topic_id_str ON telegram_users(topic_id);
        """)

        db.commit()
        LOG.info("✅ Auto-migrations completed successfully (topics UNIQUE(url_key, url))")

    except Exception as exc:
        db.rollback()
        LOG.error("❌ Auto-migration failed: %s", exc)
        raise
    finally:
        try:
            cursor.execute("SELECT pg_advisory_unlock(84729103);")
            db.commit()
            cursor.close()
        except Exception:
            pass
