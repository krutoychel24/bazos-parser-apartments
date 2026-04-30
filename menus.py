"""Inline keyboards + callback data conventions.

Callback data format: short, colon-separated. Telegram limits to 64 bytes.
Examples:
  m:main           — open main menu
  m:filters        — open filters menu
  m:stats          — open stats screen
  m:help           — show help text
  m:reset          — confirm reset
  m:reset:yes
  m:pause / m:resume / m:check
  f:edit:cenado    — open editor for filter "cenado"
  f:set:cenado:600 — set filter cenado=600
  f:clear:cenado   — clear filter
  f:custom:cenado  — prompt user for custom value
  ad:hide:<ad_id>  — mark ad hidden
  noop             — placeholder (for header rows, etc.)
"""
from __future__ import annotations

from typing import Iterable

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from storage import DEFAULT_FILTERS

# Filter key → human label
FILTER_LABELS = {
    "hlokalita": "📮 PSČ",
    "humkreis":  "📏 Радиус",
    "cenaod":    "💶 Цена от",
    "cenado":    "💶 Цена до",
    "hledat":    "🔎 Поиск",
    "order":     "↕️ Сортировка",
}

# Filter key → ordered list of preset values shown as buttons
FILTER_PRESETS: dict[str, list[tuple[str, str]]] = {
    "humkreis": [
        ("0 (точно)", "0"), ("1 км", "1"), ("2 км", "2"),
        ("5 км", "5"),     ("10 км", "10"), ("20 км", "20"),
        ("30 км", "30"),   ("50 км", "50"), ("75 км", "75"),
        ("100 км", "100"),
    ],
    "cenaod": [
        ("любая", ""),
        ("200", "200"), ("300", "300"), ("400", "400"),
        ("500", "500"), ("600", "600"), ("700", "700"),
    ],
    "cenado": [
        ("без лимита", ""),
        ("400", "400"), ("500", "500"), ("550", "550"),
        ("600", "600"), ("700", "700"), ("800", "800"),
        ("1000", "1000"),
    ],
    "hlokalita": [
        ("Košice 04001", "04001"),
        ("Košice 04011", "04011"),
        ("Bratislava 81101", "81101"),
        ("очистить", ""),
    ],
    "order": [
        ("по дате", ""),
        ("дешевле", "1"),
        ("дороже", "2"),
    ],
    # hledat — only custom input, no presets
}


def main_menu(enabled: bool) -> InlineKeyboardMarkup:
    pause_btn = (
        InlineKeyboardButton("⏸ Пауза",     callback_data="m:pause")
        if enabled else
        InlineKeyboardButton("▶️ Возобновить", callback_data="m:resume")
    )
    rows = [
        [InlineKeyboardButton("🔍 Проверить сейчас", callback_data="m:check")],
        [InlineKeyboardButton("⚙️ Фильтры", callback_data="m:filters"),
         InlineKeyboardButton("📊 Статистика", callback_data="m:stats")],
        [InlineKeyboardButton("✅ Отписанные", callback_data="m:list:contacted:0"),
         InlineKeyboardButton("👎 Не нравится", callback_data="m:list:disliked:0")],
        [pause_btn,
         InlineKeyboardButton("🧹 Сбросить кэш", callback_data="m:clearseen")],
        [InlineKeyboardButton("❓ Помощь", callback_data="m:help"),
         InlineKeyboardButton("🛑 Отписаться", callback_data="m:stop")],
    ]
    return InlineKeyboardMarkup(rows)


def _short(value: str, n: int = 18) -> str:
    if not value:
        return "—"
    return value if len(value) <= n else value[: n - 1] + "…"


def filters_menu(filters: dict) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for key in ("hlokalita", "humkreis", "cenaod", "cenado", "hledat", "order"):
        label = FILTER_LABELS[key]
        cur = _short(filters.get(key, ""))
        rows.append([
            InlineKeyboardButton(
                f"{label}: {cur}",
                callback_data=f"f:edit:{key}",
            )
        ])
    rows.append([
        InlineKeyboardButton("♻️ Сбросить всё", callback_data="m:reset"),
        InlineKeyboardButton("⬅️ Назад", callback_data="m:main"),
    ])
    return InlineKeyboardMarkup(rows)


def filter_edit_menu(key: str, current: str) -> InlineKeyboardMarkup:
    presets = FILTER_PRESETS.get(key, [])
    rows: list[list[InlineKeyboardButton]] = []
    # presets in 2-column grid, marking current
    row: list[InlineKeyboardButton] = []
    for label, value in presets:
        marker = "✅ " if value == current else ""
        row.append(InlineKeyboardButton(
            f"{marker}{label}",
            callback_data=f"f:set:{key}:{value}",
        ))
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)

    rows.append([
        InlineKeyboardButton("✏️ Ввести вручную", callback_data=f"f:custom:{key}"),
    ])
    if current:
        rows.append([InlineKeyboardButton("🗑 Очистить", callback_data=f"f:clear:{key}")])
    rows.append([InlineKeyboardButton("⬅️ К фильтрам", callback_data="m:filters")])
    return InlineKeyboardMarkup(rows)


def confirm_menu(yes_data: str, no_data: str = "m:main",
                 yes_label: str = "✅ Да", no_label: str = "❌ Отмена") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(yes_label, callback_data=yes_data),
        InlineKeyboardButton(no_label,  callback_data=no_data),
    ]])


def stats_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 Обновить", callback_data="m:stats"),
        InlineKeyboardButton("⬅️ Назад",   callback_data="m:main"),
    ]])


def help_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("⬅️ Назад", callback_data="m:main"),
    ]])


def cancel_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("❌ Отмена", callback_data="m:filters"),
    ]])


def ad_buttons(ad_id: str, ad_url: str, status: str = "new") -> InlineKeyboardMarkup:
    """Action row under an ad message.

    status: 'new' | 'contacted' | 'disliked' — drives which button shows the ✅ marker.
    """
    contacted = "✅ Отписал" if status == "contacted" else "✉️ Отписал"
    disliked  = "✅ Не нравится" if status == "disliked"  else "👎 Не нравится"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 Открыть на bazos", url=ad_url)],
        [InlineKeyboardButton(contacted, callback_data=f"ad:c:{ad_id}"),
         InlineKeyboardButton(disliked,  callback_data=f"ad:d:{ad_id}")],
        [InlineKeyboardButton("↩️ Сбросить", callback_data=f"ad:n:{ad_id}")],
    ])


def list_menu(status: str, page: int, total: int, page_size: int = 5) -> InlineKeyboardMarkup:
    """Pagination footer for the contacted/disliked lists."""
    rows: list[list[InlineKeyboardButton]] = []
    pages = max(1, (total + page_size - 1) // page_size)
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"m:list:{status}:{page-1}"))
    nav.append(InlineKeyboardButton(f"{page+1}/{pages}", callback_data="noop"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"m:list:{status}:{page+1}"))
    if len(nav) > 1:
        rows.append(nav)
    rows.append([InlineKeyboardButton("⬅️ В меню", callback_data="m:main")])
    return InlineKeyboardMarkup(rows)


def list_item_buttons(ad_id: str, ad_url: str, status: str) -> InlineKeyboardMarkup:
    """Buttons under each ad in a status list."""
    rows = [[InlineKeyboardButton("🔗 Открыть", url=ad_url)]]
    if status == "contacted":
        rows.append([InlineKeyboardButton("↩️ Не отписал", callback_data=f"ad:n:{ad_id}")])
    elif status == "disliked":
        rows.append([InlineKeyboardButton("↩️ Вернуть", callback_data=f"ad:n:{ad_id}")])
    return InlineKeyboardMarkup(rows)


def format_filters(filters: dict) -> str:
    """HTML-safe pretty print for filter values."""
    from html import escape
    lines = []
    for key in ("hlokalita", "humkreis", "cenaod", "cenado", "hledat", "order"):
        label = FILTER_LABELS[key]
        val = filters.get(key, "") or "—"
        lines.append(f"{label}: <code>{escape(str(val))}</code>")
    return "\n".join(lines)
