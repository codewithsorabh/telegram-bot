# ─────────────────────────────────────────────
#  templates / messages / plan_messages.py
# ─────────────────────────────────────────────

MANAGE_PLANS_HEADER = (
    "💳 <b>Plan Management</b>\n\nChoose an option below to create or view plans:"
)


def PLAN_DETAIL_MSG(p: dict) -> str:
    created = (
        p["created_at"].strftime("%d %b %Y, %H:%M UTC") if p.get("created_at") else "—"
    )
    updated = (
        p["updated_at"].strftime("%d %b %Y, %H:%M UTC") if p.get("updated_at") else "—"
    )
    price = p.get("price", 0.0)
    price_str = f"${price:.2f}" if price else "Free"
    return (
        f"💳 <b>Plan: {p.get('name', '—')}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 <b>ID         :</b> <code>{p['id']}</code>\n"
        f"📛 <b>Name       :</b> {p.get('name', '—')}\n"
        f"⏳ <b>Validity   :</b> <code>{p['validity_days']} days</code>\n"
        f"📋 <b>Max Topics :</b> <code>{p.get('max_topics', 1)}</code>\n"
        f"📱 <b>Max Accts  :</b> <code>{p.get('max_accounts', 1)}</code>\n"
        f"💰 <b>Price      :</b> {price_str}\n"
        f"📅 <b>Created    :</b> {created}\n"
        f"🔄 <b>Updated    :</b> {updated}"
    )
