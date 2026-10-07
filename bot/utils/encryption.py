# ─────────────────────────────────────────────
#  utils / encryption.py
#
#  Fernet symmetric encryption for Telegram
#  session strings.
#
#  .env key: ENCRYPTION_KEY
#  Generate once with:
#    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# ─────────────────────────────────────────────

import os
from cryptography.fernet import Fernet, InvalidToken
from config.logger import LOG

_key: bytes | None = None


def _get_fernet() -> Fernet:
    """Lazy-load Fernet instance from ENCRYPTION_KEY env var."""
    global _key
    if _key is None:
        raw = os.getenv("ENCRYPTION_KEY", "").strip()
        if not raw:
            new_key = Fernet.generate_key().decode()
            os.environ["ENCRYPTION_KEY"] = new_key
            raw = new_key
            try:
                env_path = os.path.join(os.getcwd(), ".env")
                if os.path.exists(env_path):
                    with open(env_path, "a", encoding="utf-8") as f:
                        f.write(f"\nENCRYPTION_KEY={new_key}\n")
                    LOG.info("🔐 Generated permanent ENCRYPTION_KEY and saved to .env")
                else:
                    LOG.warning("ENCRYPTION_KEY generated for this session")
            except Exception as exc:
                LOG.error("Failed to append ENCRYPTION_KEY to .env: %s", exc)
        _key = raw.encode() if isinstance(raw, str) else raw
    return Fernet(_key)


def encrypt_session(plain: str) -> str:
    """Encrypt a Telethon session string. Returns Base64 Fernet token."""
    if not plain:
        return plain
    # If already encrypted (starts with 'gAA'), return as-is
    if plain.startswith("gAA"):
        return plain
    f = _get_fernet()
    return f.encrypt(plain.encode()).decode()


def decrypt_session(token: str) -> str:
    """Decrypt a Fernet-encrypted session string."""
    if not token:
        return token
    # If NOT encrypted (plain StringSession doesn't start with gAA), return as-is
    # This handles legacy plaintext sessions gracefully
    if not token.startswith("gAA"):
        return token
    try:
        f = _get_fernet()
        return f.decrypt(token.encode()).decode()
    except InvalidToken:
        LOG.error("Failed to decrypt session string — key mismatch or corrupted data")
        return ""
