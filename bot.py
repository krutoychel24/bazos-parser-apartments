"""Telegram bot — bazos.sk apartment notifier with inline-menu UI."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from html import escape

from telegram import InlineKeyboardMarkup, InputMediaPhoto, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters as tg_filters,
)

import menus
from scraper import Ad, fetch, fetch_detail
from storage import DEFAULT_FILTERS, FILTER_KEYS, Storage

log = logging.getLogger(__name__)

CHECK_INTERVAL_SEC = int(os.environ.get("CHECK_INTERVAL_SEC", "600"))
DB_PATH = os.environ.get("DB_PATH", "/data/bazos.sqlite3")
MAX_NEW_PER_TICK = 10

WELCOME = (
    "👋 <b>Привет!</b>\n\n"
    "Я мониторю <a href=\"https://reality.bazos.sk\">reality.bazos.sk</a> "
    "и шлю новые объявления об аренде по твоим фильтрам.\n\n"
    "Управляй мной кнопками ниже. Команды тоже работают (см. /help)."
)

HELP_TEXT = (
    "<b>Управление</b>\n"
    "Все действия — через меню. Команды — для ленивых:\n\n"
    "/menu — открыть меню\n"
    "/start — подписаться + меню\n"
    "/check — проверить сейчас\n"
    "/filters — текущие фильтры\n"
    "/stop — отписаться\n\n"
    "<b>Как работают фильтры</b>\n"
    "• <b>PSČ</b> — почтовый индекс центральной точки (e.g. 04001 = Košice)\n"
    "• <b>Радиус</b> — км от PSČ\n"
    "• <b>Цена от/до</b> — €/мес\n"
    "• <b>Поиск</b> — фраза в заголовке/описании\n"
    "• <b>Сортировка</b> — обычно \"по дате\", не трогай\n\n"
    "Первая выдача после /start помечается как просмотренная — "
    "тебе придут только реально новые объявления."
)


def _storage(ctx: ContextTypes.DEFAULT_TYPE) -> Storage:
    return ctx.application.bot_data["storage"]


# ----------------------------------------------------------------------- #
# Screen rendering helpers
# ----------------------------------------------------------------------- #

async def _send_or_edit(
    update: Update,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    parse_mode: str = ParseMode.HTML,
) -> None:
    """Edit the menu in place if invoked from a callback, else send a new message."""
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
                disable_web_page_preview=True,
            )
            return
        except BadRequest as e:
            # "message is not modified" — harmless
            if "not modified" in str(e).lower():
                return
            # message had a photo (caption) — fall through to sending new
            log.debug("edit failed, sending new: %s", e)
    chat = update.effective_chat
    await chat.send_message(
        text=text,
        reply_markup=reply_markup,
        parse_mode=parse_mode,
        disable_web_page_preview=True,
    )


async def show_main(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                     prefix: str = "") -> None:
    chat_id = update.effective_chat.id
    storage = _storage(ctx)
    storage.add_subscriber(chat_id)
    enabled = storage.is_enabled(chat_id)
    status = "🟢 Активно" if enabled else "🟡 На паузе"
    text = (prefix + "\n\n" if prefix else "") + (
        f"<b>Главное меню</b>\nСтатус: {status}"
    )
    await _send_or_edit(update, text, menus.main_menu(enabled))


async def show_filters(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    f = _storage(ctx).get_filters(chat_id)
    text = "<b>Фильтры</b>\n\n" + menus.format_filters(f) + (
        "\n\nНажми на любой фильтр чтобы изменить."
    )
    await _send_or_edit(update, text, menus.filters_menu(f))


async def show_filter_editor(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                              key: str) -> None:
    if key not in FILTER_KEYS:
        await show_filters(update, ctx)
        return
    chat_id = update.effective_chat.id
    f = _storage(ctx).get_filters(chat_id)
    cur = f.get(key, "") or "—"
    label = menus.FILTER_LABELS.get(key, key)
    text = (
        f"<b>{label}</b>\n"
        f"Текущее значение: <code>{escape(str(cur))}</code>\n\n"
        "Выбери из пресетов или введи вручную."
    )
    await _send_or_edit(update, text, menus.filter_edit_menu(key, f.get(key, "")))


async def show_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    s = _storage(ctx).get_stats(chat_id)
    if not s:
        await _send_or_edit(update, "Ты пока не подписан. Жми /start.")
        return
    last = s["last_check"] or "—"
    created = s["created_at"] or "—"
    status = "🟢 активно" if s["enabled"] else "🟡 пауза"
    text = (
        "<b>📊 Статистика</b>\n\n"
        f"Статус: {status}\n"
        f"Подписан с: <code>{created}</code> UTC\n"
        f"Последняя проверка: <code>{last}</code> UTC\n"
        f"Тиков выполнено: <b>{s['ticks']}</b>\n"
        f"Объявлений отправлено: <b>{s['sent']}</b>\n"
        f"Ошибок: <b>{s['errors']}</b>\n"
        f"В кэше (просмотрено): <b>{s['seen_count']}</b>\n"
        f"✅ Отписал: <b>{s.get('contacted', 0)}</b>\n"
        f"👎 Не нравятся: <b>{s.get('disliked', 0)}</b>\n\n"
        f"Интервал опроса: <b>{CHECK_INTERVAL_SEC}s</b>"
    )
    await _send_or_edit(update, text, menus.stats_menu())


async def show_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await _send_or_edit(update, HELP_TEXT, menus.help_menu())


# ----------------------------------------------------------------------- #
# Command handlers
# ----------------------------------------------------------------------- #

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    storage = _storage(ctx)
    is_new = not storage.has_seen_any(chat_id)
    storage.add_subscriber(chat_id)
    storage.set_enabled(chat_id, True)
    await update.message.reply_text(
        WELCOME, parse_mode=ParseMode.HTML, disable_web_page_preview=True
    )
    await show_main(update, ctx)
    if is_new:
        try:
            ads = fetch(storage.get_filters(chat_id))
            storage.prime_seen(chat_id, [a.ad_id for a in ads])
            await update.effective_chat.send_message(
                f"🔇 Текущая выдача ({len(ads)} объявлений) помечена как просмотренная.\n"
                "Слать буду только новые."
            )
        except Exception as e:
            log.exception("prime failed: %s", e)


async def cmd_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await show_main(update, ctx)


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await show_help(update, ctx)


async def cmd_filters(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await show_filters(update, ctx)


async def cmd_check(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    storage = _storage(ctx)
    storage.add_subscriber(chat_id)
    msg = await update.message.reply_text("⏳ Проверяю…")
    sent = await _check_chat(ctx, chat_id, storage.get_filters(chat_id),
                              force_announce=True)
    await msg.edit_text("Готово. Новых объявлений нет." if sent == 0
                       else f"Готово. Отправил: {sent}")


async def cmd_stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    _storage(ctx).remove_subscriber(update.effective_chat.id)
    await update.message.reply_text("Отписан. Все данные удалены. /start чтобы вернуться.")


# ----------------------------------------------------------------------- #
# Inline-button router
# ----------------------------------------------------------------------- #

async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if q is None or q.data is None:
        return
    data = q.data
    storage = _storage(ctx)
    chat_id = q.message.chat_id

    # Always ack to remove the loading spinner.
    try:
        await q.answer()
    except TelegramError:
        pass

    if data == "noop":
        return

    # ----- main menu actions -----
    if data == "m:main":
        ctx.user_data.pop("awaiting_filter", None)
        await show_main(update, ctx)
        return
    if data == "m:filters":
        ctx.user_data.pop("awaiting_filter", None)
        await show_filters(update, ctx)
        return
    if data == "m:stats":
        await show_stats(update, ctx)
        return
    if data == "m:help":
        await show_help(update, ctx)
        return
    if data == "m:check":
        await q.answer("Проверяю…", show_alert=False)
        sent = await _check_chat(ctx, chat_id, storage.get_filters(chat_id),
                                  force_announce=True)
        msg = "Новых нет." if sent == 0 else f"Отправил: {sent}"
        await show_main(update, ctx, prefix=msg)
        return
    if data == "m:pause":
        storage.set_enabled(chat_id, False)
        await show_main(update, ctx, prefix="⏸ Поставил на паузу.")
        return
    if data == "m:resume":
        storage.add_subscriber(chat_id)
        storage.set_enabled(chat_id, True)
        await show_main(update, ctx, prefix="▶️ Возобновил.")
        return
    if data == "m:reset":
        await _send_or_edit(
            update,
            "Сбросить все фильтры на дефолтные?\n\n" + menus.format_filters(DEFAULT_FILTERS),
            menus.confirm_menu("m:reset:yes", "m:filters"),
        )
        return
    if data == "m:reset:yes":
        storage.reset_filters(chat_id)
        await show_filters(update, ctx)
        return
    if data == "m:clearseen":
        await _send_or_edit(
            update,
            "Очистить кэш просмотренных объявлений?\n"
            "После этого тебе могут снова прилететь те, что уже видел.",
            menus.confirm_menu("m:clearseen:yes", "m:main"),
        )
        return
    if data == "m:clearseen:yes":
        n = storage.clear_seen(chat_id)
        await show_main(update, ctx, prefix=f"🧹 Очистил {n} записей.")
        return
    if data == "m:stop":
        await _send_or_edit(
            update,
            "Точно отписаться? Все твои фильтры и история будут удалены.",
            menus.confirm_menu("m:stop:yes", "m:main", yes_label="🛑 Да, отписаться"),
        )
        return
    if data == "m:stop:yes":
        storage.remove_subscriber(chat_id)
        await _send_or_edit(update, "Отписан. /start чтобы вернуться.")
        return

    # ----- filter actions -----
    if data.startswith("f:edit:"):
        key = data.split(":", 2)[2]
        ctx.user_data.pop("awaiting_filter", None)
        await show_filter_editor(update, ctx, key)
        return
    if data.startswith("f:set:"):
        _, _, key, value = data.split(":", 3)
        if key in FILTER_KEYS:
            storage.update_filter(chat_id, key, value)
        await show_filter_editor(update, ctx, key)
        return
    if data.startswith("f:clear:"):
        key = data.split(":", 2)[2]
        if key in FILTER_KEYS:
            storage.update_filter(chat_id, key, "")
        await show_filter_editor(update, ctx, key)
        return
    if data.startswith("f:custom:"):
        key = data.split(":", 2)[2]
        if key not in FILTER_KEYS:
            await show_filters(update, ctx)
            return
        ctx.user_data["awaiting_filter"] = key
        label = menus.FILTER_LABELS.get(key, key)
        await _send_or_edit(
            update,
            f"✏️ Введи новое значение для <b>{label}</b> следующим сообщением.\n\n"
            "Или жми <b>Отмена</b>.",
            menus.cancel_menu(),
        )
        return

    # ----- contacted / disliked lists -----
    if data.startswith("m:list:"):
        _, _, status, page_s = data.split(":", 3)
        try:
            page = int(page_s)
        except ValueError:
            page = 0
        await show_status_list(update, ctx, status, page)
        return

    # ----- ad actions: c=contacted, d=disliked, n=reset, hide=legacy -----
    if data.startswith("ad:c:") or data.startswith("ad:d:") or data.startswith("ad:n:"):
        action = data.split(":", 2)[1]
        ad_id  = data.split(":", 2)[2]
        new_status = {"c": "contacted", "d": "disliked", "n": "new"}[action]
        storage.set_status(chat_id, ad_id, new_status)
        # Refresh buttons under the original ad message in place.
        ad_url = _ad_url(ad_id)
        try:
            await q.edit_message_reply_markup(
                reply_markup=menus.ad_buttons(ad_id, ad_url, new_status)
            )
        except (BadRequest, TelegramError):
            pass
        toast = {"contacted": "Отмечено как отписал ✅",
                 "disliked":  "Отмечено как не нравится 👎",
                 "new":       "Сброшено."}[new_status]
        await q.answer(toast, show_alert=False)
        return
    if data.startswith("ad:hide:"):  # legacy from older messages
        ad_id = data.split(":", 2)[2]
        storage.hide_ad(chat_id, ad_id)
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except TelegramError:
            pass
        await q.answer("Скрыл.", show_alert=False)
        return

    log.warning("unhandled callback data: %s", data)


def _ad_url(ad_id: str) -> str:
    """Reconstruct ad URL from ID (slug isn't strictly needed for the redirect)."""
    return f"https://reality.bazos.sk/inzerat/{ad_id}/"


async def show_status_list(
    update: Update, ctx: ContextTypes.DEFAULT_TYPE,
    status: str, page: int,
) -> None:
    if status not in ("contacted", "disliked"):
        await show_main(update, ctx)
        return
    page_size = 5
    storage = _storage(ctx)
    rows, total = storage.list_by_status(
        update.effective_chat.id, status,
        limit=page_size, offset=page * page_size,
    )
    title = "✅ <b>Отписанные</b>" if status == "contacted" else "👎 <b>Не нравятся</b>"
    if total == 0:
        text = f"{title}\n\nПока пусто."
        await _send_or_edit(update, text, menus.list_menu(status, 0, 0, page_size))
        return

    # Header (shown via edit) + then send each row as its own message-with-buttons.
    header = f"{title}\nВсего: <b>{total}</b>"
    await _send_or_edit(update, header, menus.list_menu(status, page, total, page_size))
    chat = update.effective_chat
    for r in rows:
        ad_id = r["ad_id"]
        url   = r.get("url") or _ad_url(ad_id)
        title_t  = r.get("title")    or f"Объявление {ad_id}"
        price = r.get("price")
        loc   = r.get("location") or ""
        author = r.get("author") or ""
        when  = r.get("status_at") or ""
        price_s = f"💰 {price} €" if price else "💰 —"
        author_s = f"\n👤 {escape(author)}" if author else ""
        text = (
            f"<b>{escape(title_t)}</b>\n"
            f"{price_s}  |  📍 {escape(loc)}"
            f"{author_s}\n"
            f"🕒 {escape(when)} UTC"
        )
        try:
            await chat.send_message(
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=menus.list_item_buttons(ad_id, url, status),
                disable_web_page_preview=True,
            )
        except TelegramError as e:
            log.warning("list item send failed: %s", e)


# ----------------------------------------------------------------------- #
# Free-text handler (custom filter input)
# ----------------------------------------------------------------------- #

async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    awaiting = ctx.user_data.get("awaiting_filter")
    if not awaiting:
        # Not in input mode → just point user at the menu.
        await show_main(update, ctx, prefix="ℹ️ Используй меню ниже.")
        return

    if awaiting not in FILTER_KEYS:
        ctx.user_data.pop("awaiting_filter", None)
        await show_main(update, ctx)
        return

    raw = (update.message.text or "").strip()
    # Validate by key
    err = _validate_filter(awaiting, raw)
    if err:
        await update.message.reply_text(f"❌ {err}\nПопробуй ещё раз или нажми Отмена.",
                                         reply_markup=menus.cancel_menu())
        return

    storage = _storage(ctx)
    storage.add_subscriber(update.effective_chat.id)
    storage.update_filter(update.effective_chat.id, awaiting, raw)
    ctx.user_data.pop("awaiting_filter", None)
    await update.message.reply_text(
        f"✅ Сохранил: <b>{menus.FILTER_LABELS[awaiting]}</b> = <code>{escape(raw)}</code>",
        parse_mode=ParseMode.HTML,
    )
    await show_filters(update, ctx)


def _validate_filter(key: str, value: str) -> str | None:
    """Return error string if invalid, None if OK."""
    if key in ("cenaod", "cenado"):
        if value and not value.isdigit():
            return "Цена должна быть числом (только цифры, без €)."
        if value and int(value) > 1_000_000:
            return "Цена слишком большая."
    elif key == "humkreis":
        valid = {"0", "1", "2", "5", "10", "20", "30", "50", "75", "100"}
        if value and value not in valid:
            return f"Радиус: одно из {sorted(valid, key=int)}."
    elif key == "hlokalita":
        if value and (not value.isdigit() or len(value) != 5):
            return "PSČ — 5 цифр (e.g. 04001)."
    elif key == "hledat":
        if len(value) > 100:
            return "Слишком длинная фраза (>100 символов)."
    elif key == "order":
        if value and value not in ("", "1", "2"):
            return "Сортировка: пусто (по дате), 1 (дешевле), 2 (дороже)."
    return None


# ----------------------------------------------------------------------- #
# Scraping / dispatch
# ----------------------------------------------------------------------- #

def _build_caption(ad: Ad, author: str = "") -> str:
    price_s = f"{ad.price} €" if ad.price is not None else "—"
    desc = ad.description[:300] + ("…" if len(ad.description) > 300 else "")
    author_line = f"👤 {escape(author)}\n" if author else ""
    return (
        f"<b>{escape(ad.title)}</b>\n"
        f"💰 {price_s}  |  📍 {escape(ad.location)}\n"
        f"{author_line}"
        f"{escape(desc)}\n"
        f"{ad.url}"
    )


async def _send_ad(ctx: ContextTypes.DEFAULT_TYPE, chat_id: int, ad: Ad) -> None:
    """Send all photos as a media_group, then a follow-up with action buttons.

    media_groups can hold 2..10 photos; caption shown on first item; buttons cannot
    attach to a group, so the action keyboard goes on a follow-up text message.
    """
    storage = _storage(ctx)

    images: list[str] = []
    author = ""
    try:
        detail = fetch_detail(ad.ad_id, ad.url)
        images = detail.images
        author = detail.author
    except Exception as e:
        log.warning("detail fetch failed for %s: %s", ad.ad_id, e)

    if not images and ad.image:
        images = [ad.image]
    images = images[:10]

    caption = _build_caption(ad, author=author)

    storage.mark_seen_with_meta(
        chat_id, ad.ad_id,
        title=ad.title, price=ad.price, location=ad.location,
        url=ad.url, author=author,
    )

    kb = menus.ad_buttons(ad.ad_id, ad.url, status="new")
    try:
        if len(images) >= 2:
            media = [
                InputMediaPhoto(
                    media=img,
                    caption=caption if i == 0 else None,
                    parse_mode=ParseMode.HTML if i == 0 else None,
                )
                for i, img in enumerate(images)
            ]
            await ctx.bot.send_media_group(chat_id=chat_id, media=media)
            await ctx.bot.send_message(
                chat_id=chat_id,
                text="⤴️ Действия для объявления:",
                reply_markup=kb,
            )
            return
        if len(images) == 1:
            await ctx.bot.send_photo(
                chat_id=chat_id, photo=images[0],
                caption=caption, parse_mode=ParseMode.HTML, reply_markup=kb,
            )
            return
        await ctx.bot.send_message(
            chat_id=chat_id, text=caption, parse_mode=ParseMode.HTML, reply_markup=kb,
        )
        return
    except TelegramError as e:
        log.warning("send failed for chat %s ad %s: %s", chat_id, ad.ad_id, e)

    try:
        await ctx.bot.send_message(
            chat_id=chat_id, text=caption, parse_mode=ParseMode.HTML, reply_markup=kb,
        )
    except TelegramError as e2:
        log.error("text fallback also failed: %s", e2)


async def _check_chat(
    ctx: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    filters: dict,
    force_announce: bool = False,
) -> int:
    storage = _storage(ctx)
    try:
        ads = fetch(filters)
    except Exception as e:
        log.exception("fetch failed for chat %s: %s", chat_id, e)
        storage.record_tick(chat_id, sent=0, error=True)
        return 0

    if not ads:
        storage.record_tick(chat_id, sent=0)
        return 0

    ad_ids = [a.ad_id for a in ads]
    is_first = not storage.has_seen_any(chat_id)
    if is_first and not force_announce:
        storage.prime_seen(chat_id, ad_ids)
        storage.record_tick(chat_id, sent=0)
        return 0

    unseen = storage.filter_unseen(chat_id, ad_ids)
    new_ads = [a for a in ads if a.ad_id in unseen]
    if not new_ads:
        storage.record_tick(chat_id, sent=0)
        return 0

    new_ads = list(reversed(new_ads))
    if len(new_ads) > MAX_NEW_PER_TICK:
        log.info("capping %d new → %d for chat %s", len(new_ads), MAX_NEW_PER_TICK, chat_id)
        new_ads = new_ads[-MAX_NEW_PER_TICK:]

    sent = 0
    for ad in new_ads:
        await _send_ad(ctx, chat_id, ad)
        storage.mark_seen(chat_id, [ad.ad_id])
        sent += 1
    storage.record_tick(chat_id, sent=sent)
    return sent


async def job_check_all(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    storage = _storage(ctx)
    subs = storage.active_subscribers()
    log.info("tick: %d active subscribers", len(subs))
    for chat_id, filters in subs:
        try:
            sent = await _check_chat(ctx, chat_id, filters)
            if sent:
                log.info("sent %d new ads to chat %s", sent, chat_id)
        except Forbidden:
            log.info("chat %s blocked bot, removing", chat_id)
            storage.remove_subscriber(chat_id)
        except Exception as e:
            log.exception("chat %s check failed: %s", chat_id, e)


# ----------------------------------------------------------------------- #
# App wiring
# ----------------------------------------------------------------------- #

def build_app(token: str, db_path: str) -> Application:
    app = Application.builder().token(token).build()
    app.bot_data["storage"] = Storage(db_path)

    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("menu",    cmd_menu))
    app.add_handler(CommandHandler("help",    cmd_help))
    app.add_handler(CommandHandler("filters", cmd_filters))
    app.add_handler(CommandHandler("check",   cmd_check))
    app.add_handler(CommandHandler("stop",    cmd_stop))

    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(tg_filters.TEXT & ~tg_filters.COMMAND, on_text))

    app.job_queue.run_repeating(job_check_all, interval=CHECK_INTERVAL_SEC, first=20)
    return app


async def _post_init(app: Application) -> None:
    """Set bot command list shown in Telegram UI."""
    from telegram import BotCommand
    await app.bot.set_my_commands([
        BotCommand("menu",    "Открыть меню"),
        BotCommand("start",   "Подписаться"),
        BotCommand("check",   "Проверить сейчас"),
        BotCommand("filters", "Текущие фильтры"),
        BotCommand("help",    "Помощь"),
        BotCommand("stop",    "Отписаться"),
    ])


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN env var is required")

    db_path = os.environ.get("DB_PATH", DB_PATH)
    db_dir = os.path.dirname(db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    app = build_app(token, db_path)
    app.post_init = _post_init
    log.info("starting bot, interval=%ss db=%s", CHECK_INTERVAL_SEC, db_path)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
