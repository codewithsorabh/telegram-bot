# ─────────────────────────────────────────────
#  utils / forward_validator.py
#
#  Telethon GetHistoryRequest, forward_messages,
#  and target resolution helper functions.
# ─────────────────────────────────────────────

from typing import Any
from telethon import TelegramClient
from telethon.tl.functions.messages import GetHistoryRequest
from config.logger import LOG


async def resolve_telethon_target(client: TelegramClient, dest: dict | str) -> tuple[Any, int | None]:
    """
    Resolve any Telegram target (username, public topic, private topic, invite link, ID)
    to a valid Telethon entity and optional topic_id (reply_to).
    """
    if dest == "me" or (isinstance(dest, str) and dest.strip().lower() == "me"):
        entity = await client.get_entity("me")
        return entity, None

    if isinstance(dest, str):
        from utils.url_validator import parse_url
        dest = parse_url(dest)
    elif isinstance(dest, dict) and "type" not in dest:
        from utils.url_validator import parse_url
        url_str = dest.get("url") or dest.get("raw") or ""
        dest = parse_url(url_str)

    raw_input = dest.get("raw", "")
    username = dest.get("username")
    group_id = dest.get("group_id")
    topic_id = dest.get("topic_id")
    invite_hash = dest.get("invite_hash")
    normalized = dest.get("normalized", "")

    # Clean candidate list for resolution
    candidates = []
    if username:
        candidates.extend([f"@{username}", username])
    if group_id:
        peer_id = group_id if group_id < 0 else int(f"-100{group_id}")
        candidates.append(peer_id)
    if normalized:
        candidates.append(normalized)
    if raw_input:
        candidates.append(raw_input)

    # 1. Try get_entity on each candidate
    for cand in candidates:
        try:
            entity = await client.get_entity(cand)
            if entity:
                return entity, topic_id
        except Exception:
            continue

    # 2. Try invite link if invite_hash present
    if invite_hash:
        from telethon.tl.functions.messages import ImportChatInviteRequest, CheckChatInviteRequest
        from telethon.errors import UserAlreadyParticipantError
        try:
            updates = await client(ImportChatInviteRequest(invite_hash))
            if updates and getattr(updates, "chats", None):
                return updates.chats[0], topic_id
        except UserAlreadyParticipantError:
            try:
                check = await client(CheckChatInviteRequest(invite_hash))
                ent = check.chats[0] if getattr(check, "chats", None) else check.chat
                return ent, topic_id
            except Exception:
                pass

    # 3. Fallback: Search joined user dialogs/chats
    search_term = (username or normalized or raw_input or "").strip().lower()
    clean_search = search_term.replace("https://t.me/", "").replace("http://t.me/", "").replace("@", "")
    if clean_search:
        async for dialog in client.iter_dialogs():
            ent = dialog.entity
            ent_user = (getattr(ent, "username", "") or "").lower()
            ent_title = (getattr(ent, "title", "") or "").lower()
            if ent_user and (ent_user == clean_search or f"@{ent_user}" == clean_search):
                return ent, topic_id
            if ent_title and clean_search in ent_title:
                return ent, topic_id

    target_name = username or raw_input or normalized or "unknown target"
    raise ValueError(f"Cannot find Telegram entity for '{target_name}' — please check link or ensure account has joined.")


async def _get_saved_messages(client: TelegramClient, source: Any, limit: int = 1):
    """
    Fetch message history from source using GetHistoryRequest.
    """
    try:
        saved = await client(
            GetHistoryRequest(
                peer=source,
                offset_id=0,
                offset_date=None,
                add_offset=0,
                limit=limit,
                max_id=0,
                min_id=0,
                hash=0,
            )
        )
        return saved
    except Exception as exc:
        LOG.error("Failed _get_saved_messages from %s: %s", source, exc)
        raise


async def _forward_validator(
    client: TelegramClient,
    entity: Any,
    messages: int | list[int],
    from_peer: Any = "me",
    silent: bool = False,
    reply_to: int | None = None,
):
    """
    Forward message(s) to target entity.
    Supports forum topic threads via top_msg_id when reply_to is provided.
    """
    if reply_to:
        import random
        from telethon.tl import functions
        msg_ids = [messages] if isinstance(messages, int) else messages
        random_ids = [random.randint(1, 1000000000) for _ in msg_ids]
        return await client(
            functions.messages.ForwardMessagesRequest(
                from_peer=from_peer,
                id=msg_ids,
                to_peer=entity,
                top_msg_id=reply_to,
                silent=silent,
                random_id=random_ids,
            )
        )

    return await client.forward_messages(
        entity=entity,
        messages=messages,
        from_peer=from_peer,
        silent=silent,
    )


async def find_open_forum_topic(client: TelegramClient, entity: Any) -> int | None:
    """Find the first open topic_id in a forum group."""
    try:
        from telethon.tl.functions.messages import GetForumTopicsRequest
        res = await client(
            GetForumTopicsRequest(
                peer=entity,
                offset_date=None,
                offset_id=0,
                offset_topic=0,
                limit=50,
                q="",
            )
        )
        if res and getattr(res, "topics", None):
            for t in res.topics:
                if not getattr(t, "closed", False):
                    return t.id
    except Exception as exc:
        LOG.debug("find_open_forum_topic failed: %s", exc)
    return None


async def resolve_clean_main_chat(client: TelegramClient, dest: dict | str) -> Any:
    """
    Resolve the clean main chat/channel entity for a target (stripping topic ID).
    Used as a fallback when a topic thread is closed.
    """
    if isinstance(dest, str):
        from utils.url_validator import parse_url
        dest = parse_url(dest)
    elif isinstance(dest, dict) and "type" not in dest:
        from utils.url_validator import parse_url
        url_str = dest.get("url") or dest.get("raw") or ""
        dest = parse_url(url_str)

    group_id = dest.get("group_id")
    username = dest.get("username")
    raw_input = dest.get("raw", "")

    if group_id:
        peer_id = group_id if group_id < 0 else int(f"-100{group_id}")
        return await client.get_entity(peer_id)
    if username:
        return await client.get_entity(f"@{username}")

    clean = raw_input.split("?")[0].rstrip("/").split("/")[-1]
    if clean.isdigit():
        peer_id = int(clean) if int(clean) < 0 else int(f"-100{clean}")
        return await client.get_entity(peer_id)

    return await client.get_entity(clean)


# Public aliases
get_saved_messages = _get_saved_messages
forward_validator = _forward_validator
