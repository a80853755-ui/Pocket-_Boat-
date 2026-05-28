import uuid
import asyncio
import logging
from datetime import datetime

import config
from state import state, Trade
from analysis import analyze
from pocket_option import po_client
from news import is_blackout
from trade_logger import log_trade, log_event

log = logging.getLogger(__name__)
_send_tg: callable = None   # injected from telegram_bot


def set_telegram_sender(fn):
    global _send_tg
    _send_tg = fn


async def _tg(text: str):
    if _send_tg:
        try:
            await _send_tg(text)
        except Exception as ex:
            log.warning(f"TG send error: {ex}")


def on_trade_result(local_id: str, result: str, profit: float):
    """Called from pocket_option thread when a trade closes."""
    idx = next((i for i, t in enumerate(state.active_trades) if t.id == local_id), None)
    if idx is None:
        return
    trade = state.active_trades.pop(idx)
    trade.result = result
    trade.profit = profit
    trade.close_time = datetime.utcnow().isoformat()
    state.record_result(trade)
    log_trade({"action": "closed", **trade.__dict__})

    s = state.stats
    emoji = "✅" if result == "win" else ("❌" if result == "loss" else "⚪")
    res_ar = "ربح" if result == "win" else ("خسارة" if result == "loss" else "تعادل")

    asyncio.run_coroutine_threadsafe(
        _tg(
            f"{emoji} *نتيجة الصفقة*\n"
            f"العملة: `{trade.asset}`\n"
            f"النتيجة: {res_ar}\n"
            f"الربح: ${profit:.2f}\n\n"
            f"📊 *اليوم:*\n"
            f"الصفقات: {s.total} | ✅{s.wins} ❌{s.losses}\n"
            f"نسبة الفوز: {s.win_rate:.1f}%\n"
            f"إجمالي الربح: ${s.profit:.2f}"
        ),
        asyncio.get_event_loop(),
    )


async def run_cycle():
    """Main analysis loop — runs every ANALYSIS_INTERVAL_SEC seconds."""
    while True:
        await asyncio.sleep(config.ANALYSIS_INTERVAL_SEC)
        if not state.is_running or state.is_paused:
            continue
        if not po_client.is_connected():
            continue
        if state.active_trades:
            continue

        state.reset_if_new_day()

        # Daily limits
        if state.stats.profit >= state.profit_limit:
            state.is_paused = True
            state.pause_reason = f"وصلت لحد الربح اليومي (${state.stats.profit:.2f})"
            await _tg(f"✅ وصلت لحد الربح اليومي: ${state.stats.profit:.2f}\nالبوت متوقف حتى الغد.")
            continue

        if state.stats.profit <= state.loss_limit:
            state.is_paused = True
            state.pause_reason = f"وصلت لحد الخسارة اليومية (${state.stats.profit:.2f})"
            await _tg(f"🛑 وصلت لحد الخسارة اليومية: ${state.stats.profit:.2f}\nالبوت متوقف حتى الغد.")
            continue

        for asset in state.currencies:
            if not state.is_running or state.is_paused:
                break

            # News blackout
            blocked, reason = is_blackout(asset, config.NEWS_PAUSE_BEFORE_MIN, config.NEWS_PAUSE_AFTER_MIN)
            if blocked:
                log.info(f"أخبار: {asset} — {reason}")
                continue

            candles = po_client.get_candles(asset)
            if len(candles) < 30:
                continue

            result = analyze(candles)
            if result.direction == "none":
                continue
            if result.confidence < state.min_win_rate:
                continue

            amount   = state.current_amount()
            trade_id = str(uuid.uuid4())
            trade    = Trade(
                id             = trade_id,
                asset          = asset,
                direction      = result.direction,
                amount         = amount,
                duration       = state.duration,
                open_time      = datetime.utcnow().isoformat(),
                martingale_step= state.martingale_step,
            )
            state.active_trades.append(trade)
            po_client.open_trade(asset, result.direction, amount, state.duration, trade_id)

            dir_emoji = "🟢" if result.direction == "call" else "🔴"
            dir_ar    = "CALL ↑" if result.direction == "call" else "PUT ↓"
            await _tg(
                f"{dir_emoji} *صفقة جديدة*\n"
                f"العملة: `{asset}`\n"
                f"الاتجاه: {dir_ar}\n"
                f"المبلغ: ${amount:.2f}\n"
                f"المدة: {state.duration}ث\n"
                f"الثقة: {result.confidence:.1f}%\n"
                f"RSI: {result.rsi:.1f}\n"
                f"الأسباب: {' | '.join(result.reasons)}"
            )
            log_trade({
                "action": "opened", **trade.__dict__,
                "confidence": result.confidence, "rsi": result.rsi,
            })
            break   # one trade at a time
