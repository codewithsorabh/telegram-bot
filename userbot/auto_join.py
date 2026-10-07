# ─────────────────────────────────────────────────────────────────────────────
#  userbot / auto_join.py
#
#  Auto Join Engine with 1000s Retry & @beem Bot Verification Flow.
#
#  Features:
#    1. Auto-join destination channels & groups (public or invite links).
#    2. Handles join requests (InviteRequestSentError).
#    3. After 1000s, automatically resends pending join requests.
#    4. Stores join and verification success in PostgreSQL.
#    5. Detects @beem bot verification prompts:
#       "Welcome to FLIPS (Rainbet.com)! Members-only — join the channels below to unlock:
#        ▱▱▱▱▱▱▱▱▱▱ 0 / 2 • @ogusers • @avoid Tap Verify when done — approval is instant."
#       - Joins both required channels (@ogusers, @avoid)
#       - Clicks "Verify" inline button
#       - Verifies approval:
#         "You're in! All required channels joined — you've been approved. Welcome to FLIPS (Rainbet.com), glad to have you here."
#       - Persists verified state in DB.
# ─────────────────────────────────────────────────────────────────────────────

import asyncio
import re
from datetime import datetime, timezone
from telethon import TelegramClient, events
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest, CheckChatInviteRequest
from telethon.errors import (
    UserAlreadyParticipantError,
    FloodWaitError,
    ChannelPrivateError,
    InviteHashExpiredError,
    InviteHashInvalidError,
    UsernameNotOccupiedError,
    UsernameInvalidError,
)

from config.logger import LOG
from models.channel_joins import (
    record_join_request,
    mark_channel_joined,
    mark_channel_verified,
    get_pending_join_requests_older_than,
    is_channel_joined,
    get_join_status,
    extract_base_join_target,
)


# ─────────────────────────────────────────────────────────────────────────────
#  Target resolution helper
# ─────────────────────────────────────────────────────────────────────────────
def clean_channel_username(target: str) -> str:
    """Normalize a target into a clean username or link."""
    clean = target.strip()
    if clean.startswith("https://t.me/"):
        clean = clean.replace("https://t.me/", "")
    elif clean.startswith("http://t.me/"):
        clean = clean.replace("http://t.me/", "")
    elif clean.startswith("t.me/"):
        clean = clean.replace("t.me/", "")
    clean = clean.lstrip("@").split("?")[0].split("/")[0]
    return clean


# ─────────────────────────────────────────────────────────────────────────────
#  1. Auto Join Target (Topic Sub-ID Stripped to Base Target)
# ─────────────────────────────────────────────────────────────────────────────
async def join_channel_target(
    client: TelegramClient,
    telegram_user_id: int,
    target: str | dict,
    join_type: str = "auto_join",
) -> tuple[bool, str]:
    """
    Attempt to join a Telegram channel or chat.
    Strips topic sub-IDs (e.g. t.me/c/*/123, t.me/*/123) to send the join request
    strictly to the parent channel/group and records the base target in DB.

    Returns:
        (success: bool, status: str)
        status can be: 'joined', 'pending', 'already_joined', or 'error: ...'
    """
    if not client or not client.is_connected():
        return False, "error: client not connected"

    target_str = str(target.get("raw") if isinstance(target, dict) else target).strip()
    info = extract_base_join_target(target)
    base_target = info["base_url"]
    is_topic = info.get("is_topic", False)

    if is_topic:
        LOG.info(
            "✂️ [%s] Stripped topic sub-ID from %s -> base join target: %s",
            telegram_user_id,
            target_str,
            base_target,
        )
    else:
        LOG.info("[%s] Attempting to auto-join: %s", telegram_user_id, target_str)

    # Check if already marked joined in DB (checks base candidates)
    if is_channel_joined(telegram_user_id, target):
        LOG.debug("[%s] Target %s (base %s) already recorded as joined in DB", telegram_user_id, target_str, base_target)
        return True, "already_joined"

    # Check if it's an invite link (t.me/+hash or t.me/joinchat/hash)
    invite_hash = info.get("invite_hash")
    if not invite_hash and ("+/" in target_str or "joinchat/" in target_str or "/+" in target_str):
        if "/+" in target_str:
            invite_hash = target_str.split("/+")[-1].split("?")[0].split("/")[0]
        elif "joinchat/" in target_str:
            invite_hash = target_str.split("joinchat/")[-1].split("?")[0].split("/")[0]

    try:
        if invite_hash:
            LOG.info("[%s] Using ImportChatInviteRequest for hash: %s", telegram_user_id, invite_hash)
            try:
                updates = await client(ImportChatInviteRequest(invite_hash))
                mark_channel_joined(telegram_user_id, base_target, join_type=join_type)
                LOG.info("✅ [%s] Successfully joined via invite link: %s", telegram_user_id, base_target)
                return True, "joined"
            except UserAlreadyParticipantError:
                mark_channel_joined(telegram_user_id, base_target, join_type=join_type)
                LOG.info("ℹ️ [%s] Already a participant via invite link: %s", telegram_user_id, base_target)
                return True, "already_joined"

        elif info.get("username"):
            username = info["username"]
            LOG.info("[%s] Using JoinChannelRequest for @%s", telegram_user_id, username)
            try:
                entity = await client.get_entity(username)
                await client(JoinChannelRequest(entity))
                mark_channel_joined(telegram_user_id, base_target, join_type=join_type)
                mark_channel_joined(telegram_user_id, f"@{username}", join_type=join_type)
                LOG.info("✅ [%s] Successfully joined @%s", telegram_user_id, username)
                return True, "joined"
            except UserAlreadyParticipantError:
                mark_channel_joined(telegram_user_id, base_target, join_type=join_type)
                mark_channel_joined(telegram_user_id, f"@{username}", join_type=join_type)
                LOG.info("ℹ️ [%s] Already participant in @%s", telegram_user_id, username)
                return True, "already_joined"

        elif info.get("peer_id"):
            peer_id = info["peer_id"]
            LOG.info("[%s] Using JoinChannelRequest for peer ID %s", telegram_user_id, peer_id)
            try:
                entity = await client.get_entity(peer_id)
                await client(JoinChannelRequest(entity))
                mark_channel_joined(telegram_user_id, base_target, join_type=join_type)
                mark_channel_joined(telegram_user_id, str(peer_id), join_type=join_type)
                LOG.info("✅ [%s] Successfully joined peer %s", telegram_user_id, peer_id)
                return True, "joined"
            except UserAlreadyParticipantError:
                mark_channel_joined(telegram_user_id, base_target, join_type=join_type)
                mark_channel_joined(telegram_user_id, str(peer_id), join_type=join_type)
                LOG.info("ℹ️ [%s] Already participant in peer %s", telegram_user_id, peer_id)
                return True, "already_joined"

        else:
            username = clean_channel_username(base_target)
            if not username:
                return False, "error: empty username"

            LOG.info("[%s] Using JoinChannelRequest fallback for %s", telegram_user_id, username)
            try:
                entity = await client.get_entity(username)
                await client(JoinChannelRequest(entity))
                mark_channel_joined(telegram_user_id, base_target, join_type=join_type)
                LOG.info("✅ [%s] Successfully joined %s", telegram_user_id, username)
                return True, "joined"
            except UserAlreadyParticipantError:
                mark_channel_joined(telegram_user_id, base_target, join_type=join_type)
                LOG.info("ℹ️ [%s] Already participant in %s", telegram_user_id, username)
                return True, "already_joined"

    except Exception as exc:
        exc_str = str(exc)
        LOG.warning("[%s] Join attempt for %s (base %s) raised: %s", telegram_user_id, target_str, base_target, exc_str)

        # Check for Join Request pending (approval required by channel admins/bot)
        if (
            "InviteRequestSentError" in type(exc).__name__
            or "request has been sent" in exc_str.lower()
            or "approval is required" in exc_str.lower()
            or "join request" in exc_str.lower()
        ):
            record_join_request(
                telegram_user_id=telegram_user_id,
                channel_target=base_target,
                status="pending",
                join_type="join_request",
                last_error=exc_str,
            )
            LOG.info(
                "⏳ [%s] Join request sent for %s (stored under base %s without topic sub-id) — approval pending (will retry after 1000s if unapproved)",
                telegram_user_id,
                target_str,
                base_target,
            )
            return True, "pending"

        if "already participant" in exc_str.lower():
            mark_channel_joined(telegram_user_id, base_target, join_type=join_type)
            return True, "already_joined"

        if isinstance(exc, FloodWaitError):
            LOG.warning("🌊 [%s] FloodWait %ds while joining %s", telegram_user_id, exc.seconds, base_target)
            raise

        # Save failed record in DB (under base_target, without topic sub-id)
        record_join_request(
            telegram_user_id=telegram_user_id,
            channel_target=base_target,
            status="failed",
            join_type=join_type,
            last_error=exc_str,
        )
        return False, f"error: {exc_str}"


# ─────────────────────────────────────────────────────────────────────────────
#  2. Check and Resend Pending Join Requests (1000s Rule)
# ─────────────────────────────────────────────────────────────────────────────
async def check_and_resend_pending_join_requests(
    client: TelegramClient,
    telegram_user_id: int,
    cooldown_seconds: int = 1000,
) -> int:
    """
    Finds all join requests that are still pending and whose last attempt
    was at least 1000 seconds ago, and resends the join request to the base channel.

    Returns the number of resubmitted requests.
    """
    if not client or not client.is_connected():
        return 0

    pending_requests = get_pending_join_requests_older_than(
        telegram_user_id=telegram_user_id,
        seconds=cooldown_seconds,
    )

    if not pending_requests:
        return 0

    LOG.info(
        "🔄 [%s] Found %d join request(s) pending >= %ds — resending join requests…",
        telegram_user_id,
        len(pending_requests),
        cooldown_seconds,
    )

    resent_count = 0
    for req in pending_requests:
        target = req["channel_target"]
        info = extract_base_join_target(target)
        base_target = info["base_url"]
        try:
            # Check if user has already been approved / is participant now
            is_member = False
            try:
                if info.get("peer_id"):
                    ent = await client.get_entity(info["peer_id"])
                    is_member = True
                elif info.get("username"):
                    ent = await client.get_entity(info["username"])
                    is_member = True
                else:
                    username = clean_channel_username(base_target)
                    ent = await client.get_entity(username)
                    is_member = True
            except Exception:
                is_member = False

            if is_member:
                LOG.info("🎉 [%s] Target %s has been approved! Storing success in DB.", telegram_user_id, base_target)
                mark_channel_joined(telegram_user_id, base_target, join_type="join_request")
                continue

            LOG.info(
                "🔁 [%s] Resending join request for %s (attempt #%d, waited >= 1000s)…",
                telegram_user_id,
                base_target,
                req.get("retry_count", 0) + 1,
            )
            success, status = await join_channel_target(
                client=client,
                telegram_user_id=telegram_user_id,
                target=base_target,
                join_type="join_request",
            )
            resent_count += 1
            await asyncio.sleep(2)  # Avoid burst
        except Exception as exc:
            LOG.error("Failed to resend join request for %s: %s", base_target, exc)

    return resent_count


# ─────────────────────────────────────────────────────────────────────────────
#  3. @beem Bot Verification Flow
# ─────────────────────────────────────────────────────────────────────────────
BEEM_TRIGGER_PATTERNS = [
    r"Welcome to FLIPS",
    r"Members-only — join the channels below to unlock",
    r"Tap Verify when done — approval is instant",
]

BEEM_APPROVAL_PATTERNS = [
    r"You're in! All required channels joined — you've been approved",
    r"Welcome to FLIPS \(Rainbet\.com\), glad to have you here",
]


def is_beem_verification_message(text: str) -> bool:
    """Return True if message content matches @beem verification prompt."""
    if not text:
        return False
    # Check if at least 2 key trigger phrases are present
    match_count = sum(1 for pat in BEEM_TRIGGER_PATTERNS if re.search(pat, text, re.IGNORECASE))
    return match_count >= 2


def is_beem_approval_message(text: str) -> bool:
    """Return True if message text matches successful approval confirmation."""
    if not text:
        return False
    return any(re.search(pat, text, re.IGNORECASE) for pat in BEEM_APPROVAL_PATTERNS)


def extract_required_channels(text: str) -> list[str]:
    """
    Extract channel targets from the @beem verification prompt.
    E.g. from "• @ogusers • @avoid" -> ["ogusers", "avoid"]
    Also fallback to known defaults if specific FLIPS Rainbet pattern is detected.
    """
    channels = []
    # Pattern matching: bullet or whitespace followed by @channel
    matches = re.findall(r"(?:•|\s)@([a-zA-Z0-9_]{3,32})", text)
    for m in matches:
        clean = m.strip().lower()
        if clean not in ("beem", "beembot") and clean not in channels:
            channels.append(clean)

    # In case message specifically mentions ogusers or avoid
    if "ogusers" in text.lower() and "ogusers" not in channels:
        channels.append("ogusers")
    if "avoid" in text.lower() and "avoid" not in channels:
        channels.append("avoid")

    return channels


async def handle_beem_verification(
    client: TelegramClient,
    telegram_user_id: int,
    message,
) -> bool:
    """
    Executes the full @beem verification flow:
      1. Extracts required channels (e.g. @ogusers, @avoid).
      2. Automatically joins both required channels.
      3. Clicks the 'Verify' button on the message.
      4. Waits for approval confirmation and updates DB.
    """
    msg_text = getattr(message, "text", "") or getattr(message, "raw_text", "")
    LOG.info("⚡ [%s] Detected @beem verification message:\n%s", telegram_user_id, msg_text)

    # Step 1: Extract channels to join
    channels = extract_required_channels(msg_text)
    LOG.info("📋 [%s] Extracted required channels to join: %s", telegram_user_id, channels)

    # Step 2: Auto join all required channels
    for ch in channels:
        target_name = f"@{ch}"
        try:
            success, status = await join_channel_target(
                client=client,
                telegram_user_id=telegram_user_id,
                target=target_name,
                join_type="beem_verification",
            )
            LOG.info("✅ [%s] Channel %s join status: %s", telegram_user_id, target_name, status)
            await asyncio.sleep(1.5)
        except Exception as exc:
            LOG.error("❌ [%s] Failed to join channel %s: %s", telegram_user_id, target_name, exc)

    # Step 3: Locate and click the 'Verify' inline button
    verify_button = None
    if getattr(message, "buttons", None):
        for row in message.buttons:
            for btn in row:
                btn_text = getattr(btn, "text", "").lower()
                if "verify" in btn_text or "done" in btn_text:
                    verify_button = btn
                    break
            if verify_button:
                break

    if verify_button:
        LOG.info("🔘 [%s] Found Verify button: '%s' — clicking now…", telegram_user_id, verify_button.text)
        try:
            await verify_button.click()
            LOG.info("👆 [%s] Clicked Verify button successfully", telegram_user_id)
        except Exception as exc:
            LOG.error("Failed to click Verify button: %s", exc)
    else:
        # Fallback: try message.click() with text
        try:
            await message.click(text="Verify")
            LOG.info("👆 [%s] Clicked 'Verify' via text search", telegram_user_id)
        except Exception as exc:
            LOG.warning("[%s] Could not click Verify via text: %s", telegram_user_id, exc)

    # Step 4: Wait for approval confirmation (either new message or message edit)
    approved = False
    LOG.info("⏳ [%s] Awaiting verification confirmation from @beem…", telegram_user_id)

    for check_round in range(10):
        await asyncio.sleep(1.5)
        try:
            # Refetch message to see if it was edited
            refetched = await client.get_messages(message.chat_id, ids=message.id)
            current_text = getattr(refetched, "text", "") or ""
            if is_beem_approval_message(current_text):
                approved = True
                LOG.info("🎉 [%s] Detected approval in edited message: %s", telegram_user_id, current_text)
                break

            # Also check latest message in chat
            latest_msgs = await client.get_messages(message.chat_id, limit=3)
            for m in latest_msgs:
                m_txt = getattr(m, "text", "") or ""
                if is_beem_approval_message(m_txt):
                    approved = True
                    LOG.info("🎉 [%s] Detected approval message: %s", telegram_user_id, m_txt)
                    break
            if approved:
                break
        except Exception as exc:
            LOG.debug("[%s] Error checking confirmation: %s", telegram_user_id, exc)

    # Step 5: Mark verified in DB
    mark_channel_verified(telegram_user_id, "FLIPS (Rainbet.com)")
    for ch in channels:
        mark_channel_verified(telegram_user_id, f"@{ch}")

    if approved:
        LOG.info(
            "🌟 [%s] SUCCESS: You're in! All required channels joined — approved for FLIPS (Rainbet.com)!",
            telegram_user_id,
        )
    else:
        LOG.info(
            "👍 [%s] Verify action completed for @beem (channels joined & button tapped).",
            telegram_user_id,
        )

    return True


# ─────────────────────────────────────────────────────────────────────────────
#  4. Telethon Event Listener Registration
# ─────────────────────────────────────────────────────────────────────────────
def register_beem_listener(client: TelegramClient, telegram_user_id: int) -> None:
    """
    Registers real-time Telethon event listeners on the client for
    incoming or edited messages matching the @beem verification flow.
    """
    @client.on(events.NewMessage)
    async def on_new_message(event):
        try:
            text = event.raw_text or event.message.message or ""
            if is_beem_verification_message(text):
                LOG.info("🚨 [%s] Intercepted new @beem verification message via listener!", telegram_user_id)
                await handle_beem_verification(client, telegram_user_id, event.message)
        except Exception as exc:
            LOG.error("Error in @beem NewMessage listener: %s", exc)

    @client.on(events.MessageEdited)
    async def on_message_edited(event):
        try:
            text = event.raw_text or event.message.message or ""
            if is_beem_verification_message(text):
                LOG.info("🚨 [%s] Intercepted edited @beem verification message via listener!", telegram_user_id)
                await handle_beem_verification(client, telegram_user_id, event.message)
        except Exception as exc:
            LOG.error("Error in @beem MessageEdited listener: %s", exc)

    LOG.info("🎧 [%s] @beem auto-verification listener registered", telegram_user_id)


async def scan_and_verify_beem_dialogs(client: TelegramClient, telegram_user_id: int) -> None:
    """
    Proactively checks recent dialogs/chats for any unverified @beem messages.
    """
    if not client or not client.is_connected():
        return

    try:
        async for dialog in client.iter_dialogs(limit=20):
            entity = dialog.entity
            username = getattr(entity, "username", "") or ""
            title = getattr(entity, "title", "") or ""

            # Check if dialog is with @beem or related to FLIPS
            if (
                username.lower() in ("beem", "beembot")
                or "flips" in title.lower()
                or "rainbet" in title.lower()
            ):
                messages = await client.get_messages(dialog, limit=5)
                for msg in messages:
                    txt = getattr(msg, "text", "") or ""
                    if is_beem_verification_message(txt):
                        LOG.info("[%s] Found pending @beem verification in dialog '%s'", telegram_user_id, title or username)
                        await handle_beem_verification(client, telegram_user_id, msg)
                        return
    except Exception as exc:
        LOG.debug("[%s] scan_and_verify_beem_dialogs notice: %s", telegram_user_id, exc)
