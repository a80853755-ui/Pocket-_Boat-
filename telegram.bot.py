import logging
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ContextTypes, filters,
)
import config
from state import state
from news import upcoming_high_impact
from pocket_option import po_client
from trade_logger import log_event

log = logging.getLogger(__name__)
_app: Application | None = None


# ── Keyboard ────────────────────────────────────────────────────────────────

def _keyboard() -> ReplyKeyboardMarkup:
    btn = KeyboardButton
    running = state.is_running and not state.is_paused
    return ReplyKeyboardMarkup(
        [
            [btn("⏹ إيقاف البوت" if running else "▶️ تشغيل البوت"), btn("📊 إحصائيات")],
            [btn("💰 تغيير المبلغ"),   btn("⏱ تغيير المدة")],
            [btn("🔄 مارتينجال"),        btn("💹 تغيير العملات")],
            [btn("📰 أخبار قادمة"),     btn("⚙️ الإعدادات")],
        ],
        resize_keyboard=True,
    )


def _stats_text() -> str:
    s   = state.stats
    st  = "✅ يعمل" if (state.is_running and not state.is_paused) else \
          ("⏸ موقوف مؤقتاً" if state.is_paused else "🔴 متوقف")
    mg  = "✅ مفعّل" if state.martingale_on else "❌ مغلق"
    ws  = "🟢 متصل" if po_client.is_connected() else "🔴 غير متصل"
    return (
        f"📊 *إحصائيات اليوم — {s.date}*\n"
        f"الحالة: {st} | PO: {ws}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"الصفقات: {s.total}\n"
        f"✅ ربح: {s.wins} | ❌ خسارة: {s.losses} | ⚪ تعادل: {s.draws}\n"
        f"نسبة الفوز: {s.win_rate:.1f}%\n"
        f"إجمالي الربح: ${s.profit:.2f}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"⚙️ *الإعدادات*\n"
        f"المبلغ: ${state.amount} | المدة: {state.duration}ث\n"
        f"حد الربح: ${state.profit_limit} | حد الخسارة: ${state.loss_limit}\n"
        f"مارتينجال: {mg}\n"
        f"العملات:\n`{chr(10).join(state.currencies)}`"
    )


# ── Auth check ───────────────────────────────────────────────────────────────

def _authorized(update: Update) -> bool:
    if not config.TELEGRAM_CHAT_ID:
        return True
    return str(update.effective_chat.id) == config.TELEGRAM_CHAT_ID


# ── Handlers ─────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update): return
    if not state.is_running:
        state.is_running = True
        state.is_paused  = False
        state.pause_reason = ""
        po_client.connect()
        log_event("bot_started")
        await update.message.reply_text(
            "✅ *البوت يعمل الآن!*\nسيبدأ تحليل الأسواق وفتح الصفقات.",
            parse_mode="Markdown", reply_markup=_keyboard(),
        )
    elif state.is_paused:
        state.is_paused = False
        state.pause_reason = ""
        await update.message.reply_text(
            "✅ *تم استئناف البوت!*",
            parse_mode="Markdown", reply_markup=_keyboard(),
        )
    else:
        await update.message.reply_text(
            "ℹ️ البوت يعمل بالفعل.", reply_markup=_keyboard(),
        )


async def cmd_stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update): return
    state.is_running = False
    state.is_paused  = False
    po_client.disconnect()
    log_event("bot_stopped")
    await update.message.reply_text(
        "🔴 *تم إيقاف البوت.*",
        parse_mode="Markdown", reply_markup=_keyboard(),
    )


async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update): return
    await update.message.reply_text(
        _stats_text(), parse_mode="Markdown", reply_markup=_keyboard(),
    )


async def cmd_set_amount(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update): return
    try:
        val = float(ctx.args[0])
        assert val > 0
        state.amount = val
        await update.message.reply_text(
            f"✅ تم تغيير المبلغ إلى ${val}", reply_markup=_keyboard(),
        )
    except Exception:
        await update.message.reply_text(
            "❌ استخدم: `/setamount 5`", parse_mode="Markdown",
        )


async def cmd_set_duration(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update): return
    try:
        val = int(ctx.args[0])
        assert val >= 30
        state.duration = val
        await update.message.reply_text(
            f"✅ تم تغيير المدة إلى {val}ث ({val/60:.1f} دقيقة)",
            reply_markup=_keyboard(),
        )
    except Exception:
        await update.message.reply_text(
            "❌ استخدم: `/setduration 60`", parse_mode="Markdown",
        )


async def cmd_set_currencies(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update): return
    try:
        raw = " ".join(ctx.args)
        currencies = [c.strip() for c in raw.replace(",", " ").split() if c.strip()]
        assert currencies
        state.currencies = currencies
        po_client.subscribe_assets(currencies)
        await update.message.reply_text(
            f"✅ تم تحديث العملات:\n" + "\n".join(currencies),
            reply_markup=_keyboard(),
        )
    except Exception:
        await update.message.reply_text(
            "❌ استخدم: `/setcurrencies EURUSD_otc BTCUSD_otc`",
            parse_mode="Markdown",
        )


async def cmd_set_limits(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update): return
    try:
        profit = float(ctx.args[0])
        loss   = float(ctx.args[1])
        state.profit_limit = profit
        state.loss_limit   = loss
        await update.message.reply_text(
            f"✅ حد الربح: ${profit} | حد الخسارة: ${loss}",
            reply_markup=_keyboard(),
        )
    except Exception:
        await update.message.reply_text(
            "❌ استخدم: `/setlimits 20 -10`", parse_mode="Markdown",
        )


async def cmd_martingale(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update): return
    state.martingale_on = not state.martingale_on
    st = "✅ مفعّل" if state.martingale_on else "❌ مغلق"
    await update.message.reply_text(
        f"🔄 *مارتينجال: {st}*\n"
        f"المضاعف: {config.MARTINGALE_MULTIPLIER}x | الحد: {config.MAX_MARTINGALE_STEPS} خطوات",
        parse_mode="Markdown", reply_markup=_keyboard(),
    )


async def cmd_news(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update): return
    events = upcoming_high_impact(4)
    if not events:
        await update.message.reply_text("✅ لا توجد أخبار قوية خلال 4 ساعات القادمة.")
        return
    lines = [
        f"🔴 {e['time'].strftime('%H:%M')} — {e['currency']}: {e['title']}"
        for e in events
    ]
    await update.message.reply_text(
        "📰 *أخبار قوية قادمة:*\n\n" + "\n".join(lines),
        parse_mode="Markdown",
    )


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update): return
    await update.message.reply_text(
        "*الأوامر المتاحة:*\n"
        "/start — تشغيل البوت\n"
        "/stop — إيقاف البوت\n"
        "/stats — إحصائيات اليوم\n"
        "/setamount 5 — تغيير المبلغ\n"
        "/setduration 60 — تغيير المدة (ثانية)\n"
        "/setcurrencies EURUSD\\_otc BTCUSD\\_otc — تغيير العملات\n"
        "/setlimits 20 -10 — حدود الربح والخسارة\n"
        "/martingale — تفعيل/إيقاف مارتينجال\n"
        "/news — أخبار قوية قادمة",
        parse_mode="Markdown", reply_markup=_keyboard(),
    )


async def handle_buttons(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update): return
    text = update.message.text or ""
    if "تشغيل" in text:   await cmd_start(update, ctx)
    elif "إيقاف" in text: await cmd_stop(update, ctx)
    elif "إحصائيات" in text or "الإعدادات" in text: await cmd_stats(update, ctx)
    elif "مارتينجال" in text:  await cmd_martingale(update, ctx)
    elif "أخبار" in text:      await cmd_news(update, ctx)
    elif "المبلغ" in text:
        await update.message.reply_text(
            f"المبلغ الحالي: ${state.amount}\n\nأرسل: `/setamount <المبلغ>`",
            parse_mode="Markdown",
        )
    elif "المدة" in text:
        await update.message.reply_text(
            f"المدة الحالية: {state.duration}ث\n\nأرسل: `/setduration <الثواني>`",
            parse_mode="Markdown",
        )
    elif "العملات" in text:
        await update.message.reply_text(
            f"العملات الحالية:\n`{'، '.join(state.currencies)}`\n\n"
            "أرسل: `/setcurrencies EURUSD_otc BTCUSD_otc`",
            parse_mode="Markdown",
        )


# ── Public send function ──────────────────────────────────────────────────────

async def send_message(text: str):
    if not _app or not config.TELEGRAM_CHAT_ID:
        return
    try:
        await _app.bot.send_message(
            chat_id=config.TELEGRAM_CHAT_ID,
            text=text,
            parse_mode="Markdown",
        )
    except Exception as ex:
        log.warning(f"send_message error: {ex}")


# ── Init ─────────────────────────────────────────────────────────────────────

def build_app() -> Application:
    global _app
    app = Application.builder().token(config.TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start",        cmd_start))
    app.add_handler(CommandHandler("stop",         cmd_stop))
    app.add_handler(CommandHandler("stats",        cmd_stats))
    app.add_handler(CommandHandler("setamount",    cmd_set_amount))
    app.add_handler(CommandHandler("setduration",  cmd_set_duration))
    app.add_handler(CommandHandler("setcurrencies",cmd_set_currencies))
    app.add_handler(CommandHandler("setlimits",    cmd_set_limits))
    app.add_handler(CommandHandler("martingale",   cmd_martingale))
    app.add_handler(CommandHandler("news",         cmd_news))
    app.add_handler(CommandHandler("help",         cmd_help))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_buttons))
    _app = app
    return app
