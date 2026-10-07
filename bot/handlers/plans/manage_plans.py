# ─────────────────────────────────────────────
#  handlers / plans / manage_plans.py — DEPRECATED (Plans Removed)
# ─────────────────────────────────────────────

from telegram.ext import ConversationHandler

def build_manage_plans_conv() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[],
        states={},
        fallbacks=[],
        allow_reentry=True,
    )

MANAGE_PLANS_HANDLERS = []
