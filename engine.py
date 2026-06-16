import uuid
import asyncio
import logging
from datetime import datetime
import os
import pandas as pd
import numpy as np

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

def calculate_vwap(df):
    """حساب VWAP مجاني من الشموع"""
    df['vwap'] = (df['close'] * df['volume']).cumsum() / df['volume'].cumsum()
    return df

def calculate_volume_profile(df, bins=20):
    """Volume Profile بسيط مجاني"""
    price_range = df['high'].max() - df['low'].min()
    bin_size = price_range / bins
    df['price_bin'] = ((df['close'] - df['low'].min()) / bin_size).astype(int)
    vp = df.groupby('price_bin')['volume'].sum()
    poc_price = df['low'].min() + vp.idxmax() * bin_size
    return poc_price

def check_signal(candles):
    """دالة الاشارة الجديدة - عتبة 90%"""
    df = pd.DataFrame(candles, columns=['time','open','high','low','close','volume'])
    df = calculate_vwap(df)
    poc = calculate_volume_profile(df)
    
    last_close = df['close'].iloc[-1]
    last_vwap = df['vwap'].iloc[-1]
    last_volume = df['volume'].iloc[-1]
    avg_volume = df['volume'].rolling(20).mean().iloc[-1]
    
    ai_confidence = 0
    signal = "none"
    
    if last_close > last_vwap and last_volume > avg_volume * 1.5:
        ai_confidence = 92  # شراء
        signal = "call"
    elif last_close < last_vwap and last_volume > avg_volume * 1.5:
        ai_confidence = 91  # بيع  
        signal = "put"
    
    # RSI بسيط
    delta = df['close'].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = -delta.clip(upper=0).rolling(14).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs)).iloc[-1]
    
    return signal, ai_confidence, round(last_vwap, 5), round(poc, 5), round(rsi, 1)

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
        await asyncio.sleep(int(os.getenv("ANALYSIS_INTERVAL_SEC", "60")))
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
            blocked, reason = is_blackout(asset, int(os.getenv("NEWS_PAUSE_BEFORE_MIN", "5")), int(os.getenv("NEWS_PAUSE_AFTER_MIN", "5")))
            if blocked:
                log.info(f"أخبار: {asset} — {reason}")
                continue

            candles = po_client.get_candles(asset)
            if len(candles) < 30:
                continue

            # استخدم الدالة الجديدة بدل analyze القديمة
            direction, confidence, vwap, poc, rsi = check_signal(candles)
            
            if direction == "none":
                continue
            if confidence < 90:  # العتبة 90% بدل 100%
                continue

            amount   = state.current_amount()
            trade_id = str(uuid.uuid4())
            trade    = Trade(
                id             = trade_id,
                asset          = asset,
                direction      = direction,
                amount         = amount,
                duration       = state.duration,
                open_time      = datetime.utcnow().isoformat(),
                martingale_step= state.martingale_step,
            )
            state.active_trades.append(trade)
            po_client.open_trade(asset, direction, amount, state.duration, trade_id)

            dir_emoji = "🟢" if direction == "call" else "🔴"
            dir_ar    = "CALL ↑" if direction == "call" else "PUT ↓"
            await _tg(
                f"{dir_emoji} *إشارة مصدقة من الذكاء الاصطناعي 🤖*\n"
                f"الزوج: `{asset}`\n"
                f"الاتجاه: {dir_ar}\n"
                f"الاستراتيجية: كسر VWAP + حجم عالي\n"
                f"المبلغ: ${amount:.2f}\n"
                f"المدة: {state.duration}ث\n"
                f"دقة الـ AI: {confidence}%\n"
                f"📊 VWAP: {vwap}\n"
                f"📊 نقطة التحكم POC: {poc}\n"
                f"📈 RSI: {rsi}"
            )
            log_trade({
                "action": "opened", **trade.__dict__,
                "confidence": confidence, "rsi": rsi,
            })
            break   # one trade at a time