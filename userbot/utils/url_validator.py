# utils / url_validator.py
#
#  Parses and validates every Telegram URL variant, returning
#  a structured dict per entry. Non-Telegram URLs are rejected.
#
#  Supported types
#  ───────────────
#  Telegram:
#    tg_username       → @handle  or  t.me/handle
#    tg_group          → t.me/groupname  (public group/channel)
#    tg_topic_public   → t.me/groupname/TOPIC_ID
#    tg_topic_public_sub  → t.me/groupname/TOPIC_ID/MSG_ID
#    tg_topic_private  → t.me/c/GROUP_ID/TOPIC_ID
#    tg_topic_private_sub → t.me/c/GROUP_ID/TOPIC_ID/MSG_ID
#    tg_invite         → t.me/+HASH  or  t.me/joinchat/HASH
#    tg_group_id       → raw numeric group id  (-100xxxxxxxxxx)
#    tg_topic_id       → raw numeric topic id  (positive int)
#
#  Unknown / invalid / non-Telegram inputs get type="invalid".

import re
from urllib.parse import urlparse
from typing import TypedDict, Literal

# ── Type alias for a parsed URL entry ────────
UrlType = Literal[
    "tg_username",
    "tg_topic_public",
    "tg_topic_public_sub",
    "tg_topic_private",
    "tg_topic_private_sub",
    "tg_invite",
    "tg_group_id",
    "tg_topic_id",
    "invalid",
]


class UrlEntry(TypedDict, total=False):
    type: UrlType
    raw: str  # original input string (unchanged)
    normalized: str  # canonical URL
    # -- Telegram fields --
    username: str
    group_id: int
    topic_id: int
    message_id: int
    invite_hash: str
    # -- Error --
    error: str


# ─────────────────────────────────────────────
#  Internal regex patterns (Telegram only)
# ─────────────────────────────────────────────
_TG_HOST = r"(?:t\.me|telegram\.me|telegram\.dog)"

_RE_TG_INVITE_NEW = re.compile(rf"https?://{_TG_HOST}/\+([A-Za-z0-9_-]+)", re.I)
_RE_TG_INVITE_OLD = re.compile(rf"https?://{_TG_HOST}/joinchat/([A-Za-z0-9_-]+)", re.I)
_RE_TG_PRIV_SUB = re.compile(rf"https?://{_TG_HOST}/c/(\d+)/(\d+)/(\d+)", re.I)
_RE_TG_PRIV = re.compile(rf"https?://{_TG_HOST}/c/(\d+)/(\d+)", re.I)
_RE_TG_PUB_SUB = re.compile(
    rf"https?://{_TG_HOST}/([A-Za-z0-9_]{{4,}})/(\d+)/(\d+)", re.I
)
_RE_TG_PUB_TOPIC = re.compile(rf"https?://{_TG_HOST}/([A-Za-z0-9_]{{4,}})/(\d+)", re.I)
_RE_TG_USERNAME_URL = re.compile(rf"https?://{_TG_HOST}/([A-Za-z0-9_]{{4,}})", re.I)
_RE_TG_AT = re.compile(r"^@([A-Za-z0-9_]{4,})$")
_RE_TG_GROUP_ID = re.compile(r"^-100(\d{7,})$")
_RE_TG_TOPIC_ID = re.compile(r"^\d{5,}$")  # bare numeric topic/message id


# ─────────────────────────────────────────────
#  Public API
# ─────────────────────────────────────────────
def parse_url(raw: str) -> UrlEntry:
    """
    Parse a single raw URL / username / ID string into a structured UrlEntry.
    Only Telegram links, @usernames, group IDs, and topic IDs are supported.

    Parameters
    ----------
    raw : str
        Telegram URLs, @usernames, group IDs, topic IDs.

    Returns
    -------
    UrlEntry dict with at minimum 'type', 'raw', and 'normalized'.
    Returns type='invalid' with an 'error' key if parsing fails or URL is not Telegram.
    """
    s = raw.strip()

    # ── Telegram: @username mention ──────────────────────────────────────
    m = _RE_TG_AT.match(s)
    if m:
        uname = m.group(1).lower()
        return UrlEntry(
            type="tg_username",
            raw=s,
            normalized=f"https://t.me/{uname}",
            username=uname,
        )

    # ── Telegram: raw group ID  (-100xxxxxxxxxx) ─────────────────────────
    m = _RE_TG_GROUP_ID.match(s)
    if m:
        gid = int(s)
        return UrlEntry(
            type="tg_group_id",
            raw=s,
            normalized=s,
            group_id=gid,
        )

    # ── Telegram: bare numeric topic/message ID ───────────────────────────
    m = _RE_TG_TOPIC_ID.match(s)
    if m:
        return UrlEntry(
            type="tg_topic_id",
            raw=s,
            normalized=s,
            topic_id=int(s),
        )

    # ── URL-based parsing ─────────────────────────────────────────────────
    url = s if s.startswith("http") else f"https://{s}"

    try:
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError("not a URL")
    except Exception:
        return UrlEntry(
            type="invalid",
            raw=s,
            normalized=s,
            error="Cannot parse as Telegram URL or ID",
        )

    # Verify that the URL host matches a Telegram host
    netloc = parsed.netloc.lower()
    is_tg = any(h in netloc for h in ["t.me", "telegram.me", "telegram.dog"])
    if not is_tg:
        return UrlEntry(
            type="invalid",
            raw=s,
            normalized=url,
            error="Non-Telegram URLs are not allowed",
        )

    # ── Telegram invite: t.me/+HASH ──────────────────────────────────────
    m = _RE_TG_INVITE_NEW.match(url)
    if m:
        return UrlEntry(
            type="tg_invite",
            raw=s,
            normalized=url,
            invite_hash=m.group(1),
        )

    # ── Telegram invite: t.me/joinchat/HASH ─────────────────────────────
    m = _RE_TG_INVITE_OLD.match(url)
    if m:
        return UrlEntry(
            type="tg_invite",
            raw=s,
            normalized=f"https://t.me/+{m.group(1)}",
            invite_hash=m.group(1),
        )

    # ── Telegram private topic+sub: t.me/c/GID/TID/MID ──────────────────
    m = _RE_TG_PRIV_SUB.match(url)
    if m:
        return UrlEntry(
            type="tg_topic_private_sub",
            raw=s,
            normalized=url,
            group_id=int(m.group(1)),
            topic_id=int(m.group(2)),
            message_id=int(m.group(3)),
        )

    # ── Telegram private topic: t.me/c/GID/TID ──────────────────────────
    m = _RE_TG_PRIV.match(url)
    if m:
        return UrlEntry(
            type="tg_topic_private",
            raw=s,
            normalized=url,
            group_id=int(m.group(1)),
            topic_id=int(m.group(2)),
        )

    # ── Telegram public topic+sub: t.me/name/TID/MID ────────────────────
    m = _RE_TG_PUB_SUB.match(url)
    if m:
        return UrlEntry(
            type="tg_topic_public_sub",
            raw=s,
            normalized=url,
            username=m.group(1).lower(),
            topic_id=int(m.group(2)),
            message_id=int(m.group(3)),
        )

    # ── Telegram public topic: t.me/name/TID ────────────────────────────
    m = _RE_TG_PUB_TOPIC.match(url)
    if m:
        return UrlEntry(
            type="tg_topic_public",
            raw=s,
            normalized=url,
            username=m.group(1).lower(),
            topic_id=int(m.group(2)),
        )

    # ── Telegram public username/group: t.me/name ────────────────────────
    m = _RE_TG_USERNAME_URL.match(url)
    if m:
        uname = m.group(1).lower()
        return UrlEntry(
            type="tg_username",
            raw=s,
            normalized=f"https://t.me/{uname}",
            username=uname,
        )

    return UrlEntry(
        type="invalid",
        raw=s,
        normalized=s,
        error=f"Unrecognised Telegram format: '{s}'",
    )


def parse_urls(raws: list[str]) -> list[UrlEntry]:
    """Parse a list of raw strings, returning one UrlEntry per item."""
    return [parse_url(r) for r in raws]


def is_valid(entry: UrlEntry) -> bool:
    """Return True if the entry was recognised (not 'invalid')."""
    return entry["type"] != "invalid"


def label(entry: UrlEntry | str | dict) -> str:
    """Human-readable one-line summary of a UrlEntry or raw URL string."""
    if isinstance(entry, str):
        entry = parse_url(entry)
    elif not isinstance(entry, dict):
        return str(entry)

    t = entry.get("type", "invalid")
    labels = {
        "tg_username": "@{username}",
        "tg_topic_public": "{username}/{topic_id}",
        "tg_topic_public_sub": "{username}/{topic_id}/msg#{message_id}",
        "tg_topic_private": "c/{group_id}/{topic_id}",
        "tg_topic_private_sub": "c/{group_id}/{topic_id}/msg#{message_id}",
        "tg_invite": "+{invite_hash}",
        "tg_group_id": "{group_id}",
        "tg_topic_id": "{topic_id}",
        "invalid": "❌ Invalid: {raw}",
    }
    tmpl = labels.get(t, "{normalized}")
    try:
        return tmpl.format(**entry)
    except Exception:
        return entry.get("normalized") or entry.get("raw") or str(entry)
