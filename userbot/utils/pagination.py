# ─────────────────────────────────────────────
#  utils / pagination.py
#
#  Reusable pagination helper for Telegram inline keyboards.
# ─────────────────────────────────────────────

from math import ceil
from telegram import InlineKeyboardButton


def paginate(items: list, page: int = 1, page_size: int | None = None) -> dict:
    """
    Paginate a list of items.

    Returns a dict containing:
      - 'items': slice of items for the requested page
      - 'page': current valid 1-indexed page number
      - 'total_pages': total number of pages (min 1)
      - 'total_items': total length of items list
      - 'has_prev': bool
      - 'has_next': bool
      - 'start_idx': starting index (1-indexed for display)
      - 'end_idx': ending index (1-indexed for display)
    """
    if page_size is None or page_size <= 0:
        from models.config import get_page_size

        page_size = get_page_size()

    total_items = len(items)
    page_size = max(1, page_size)
    total_pages = max(1, ceil(total_items / page_size)) if total_items > 0 else 1

    # Clamp page number between 1 and total_pages
    current_page = max(1, min(page, total_pages))

    start_offset = (current_page - 1) * page_size
    end_offset = start_offset + page_size
    paged_items = items[start_offset:end_offset]

    return {
        "items": paged_items,
        "page": current_page,
        "total_pages": total_pages,
        "total_items": total_items,
        "has_prev": current_page > 1,
        "has_next": current_page < total_pages,
        "start_idx": start_offset + 1 if total_items > 0 else 0,
        "end_idx": min(end_offset, total_items),
    }


def create_pagination_row(
    current_page: int,
    total_pages: int,
    callback_prefix: str,
) -> list[InlineKeyboardButton]:
    """
    Create a standard pagination row of InlineKeyboardButtons.

    Example output row:
      [ ◀️ Prev | 📄 2/5 | Next ▶️ ]

    Args:
        current_page: Current page number (1-indexed)
        total_pages: Total number of pages
        callback_prefix: Prefix for callback data (e.g. 'view_users_all:page')
    """
    if total_pages <= 1:
        return []

    row = []

    # Prev Button
    if current_page > 1:
        row.append(
            InlineKeyboardButton(
                "◀️ Prev", callback_data=f"{callback_prefix}:{current_page - 1}"
            )
        )
    else:
        row.append(InlineKeyboardButton("⛔", callback_data="noop"))

    # Page Indicator (non-interactive display)
    row.append(
        InlineKeyboardButton(f"📄 {current_page}/{total_pages}", callback_data="noop")
    )

    # Next Button
    if current_page < total_pages:
        row.append(
            InlineKeyboardButton(
                "Next ▶️", callback_data=f"{callback_prefix}:{current_page + 1}"
            )
        )
    else:
        row.append(InlineKeyboardButton("⛔", callback_data="noop"))

    return row
