# ─────────────────────────────────────────────
#  templates / messages / topic_messages.py
# ─────────────────────────────────────────────

MANAGE_TOPICS_HEADER = (
    "📋 <b>Topic Management</b>\n\nChoose an option below to create or view topics:"
)

ASK_TOPIC_NAME = (
    "📋 <b>Add Topic — Step 1 / 2</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━\n"
    "📛 <b>Topic Name</b>\n\n"
    "Enter a name for this topic (e.g. <code>Instagram Markets</code>):"
)

ASK_TOPIC_URLS = (
    "📋 <b>Add Topic — Step 2 / 2</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━\n"
    "🔗 <b>Telegram URLs / Usernames</b>\n\n"
    "Enter the Telegram links or @usernames <b>line-by-line</b>:\n\n"
    "<i>Non-Telegram links are automatically ignored.</i>"
)


def TOPIC_DETAIL_MSG(t: dict) -> str:
    from utils.url_validator import label

    url_text = label(t.get("url", ""))
    status_icon = "🟢 Active" if t.get("enabled", True) else "🔴 Disabled"
    created = (
        t["created_at"].strftime("%d %b %Y, %H:%M UTC") if t.get("created_at") else "—"
    )

    return (
        f"📋 <b>Topic: {t['name']}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔑 <b>url_key :</b> <code>{t['url_key']}</code>\n"
        f"🆔 <b>ID :</b> <code>{t['id']}</code>\n"
        f"📡 <b>Status :</b> {status_icon}\n"
        f"⏱️ <b>Interval :</b> <code>{t['interval_seconds']}s</code>\n"
        f"📅 <b>Created :</b> {created}\n\n"
        f"🔗 <b>URL :</b>\n{url_text}"
    )


def TOPIC_GROUP_DETAIL_MSG(url_key: str, entries: list[dict]) -> str:
    from utils.url_validator import label

    if not entries:
        return f"📋 <b>Topic Category: {url_key}</b>\n\n<i>No URLs saved yet.</i>"

    name = entries[0]["name"]
    urls_str = ""
    for i, e in enumerate(entries, 1):
        status_flag = "✔" if e.get("enabled", True) else "✗"
        urls_str += f"{status_flag} <b>{i}.</b> {label(e.get('url', ''))} <i>({e.get('interval_seconds', 60)}s)</i>\n"

    return (
        f"📋 <b>Topic: {name}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔑 <b>url_key :</b> <code>{url_key}</code>\n"
        f"🔗 <b>Saved URLs ({len(entries)}):</b>\n"
        f"{urls_str}"
    )


# Legacy aliases
MANAGE_GROUPS_HEADER = MANAGE_TOPICS_HEADER
ASK_GROUP_NAME = ASK_TOPIC_NAME
ASK_GROUP_URLS = ASK_TOPIC_URLS
