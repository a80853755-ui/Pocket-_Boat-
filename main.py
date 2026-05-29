import os
import asyncio
import requests
import pandas as pd
import ta
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

TOKEN = os.getenv("TOKEN")
TWELVE_API = os.getenv("TWELVE_API_KEY")
CHAT_ID = os.getenv("CHAT_ID")

PAIRS = {
    "EUR/USD": "EUR/USD",
    "GBP/USD": "GBP/USD",
    "USD/JPY": "USD/JPY",
    "EUR/USD OTC": "EUR/USD:OTC",
    "GBP/USD OTC": "GBP/USD:OTC"
}

last_signals = {}

async def get_candles(symbol, interval="1min", outputsize=100):
    url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval={interval}&outputsize={outputsize}&apikey={TWELVE_API}"
    try:
        data = requests.get(url, timeout=10).json()
        if 'values' not in data:
            return None
        df = pd.DataFrame(data['values'])
        df = df.iloc[::-1].reset_index(drop=True)
        df[['open','high','low','close','volume']] = df[['open','high','low','close','volume']].astype(float)
        return df
    except:
        return None

def check_confirmed_patterns(df):
    if len(df) < 3:
        return None, 0, ""
    
    c1 = df.iloc[-3]
    c2 = df.iloc[-2]
    c3 = df.iloc[-1]
    
    if c1['close'] < c1['open'] and c2['close'] > c2['open'] and c2['close'] > c1['open'] and c2['open'] < c1['close'] and c2['close'] > c1['high']:
        return "CALL", 30, "Bullish Engulfing مؤكد"
    
    if c1['close'] > c1['open'] and c2['close'] < c2['open'] and c2['close'] < c1['open'] and c2['open'] > c1['close'] and c2['close'] < c1['low']:
        return "PUT", 30, "Bearish Engulfing مؤكد"
    
    if c1['close'] < c1['open'] and c2['close'] > c2['open'] and c2['open'] < c1['low'] and c2['close'] > (c1['open'] + c1['close']) / 2 and c2['close'] < c1['open']:
        return "CALL", 28, "Piercing Line مؤكد"
    
    if c1['close'] > c1['open'] and c2['close'] < c2['open'] and c2['open'] > c1['high'] and c2['close'] < (c1['open'] + c1['close']) / 2 and c2['close'] > c1['open']:
        return "PUT", 28, "Dark Cloud Cover مؤكد"
    
    body1 = abs(c1['close'] - c1['open'])
    lower1 = c1['open'] - c1['low'] if c1['close'] > c1['open'] else c1['close'] - c1['low']
    if lower1 > 2 * body1 and c1['low'] <= df['low'].rolling(10).min().iloc[-3] and c2['close'] > c2['open'] and c2['close'] > c1['high']:
        return "CALL", 25, "Hammer مؤكد + كسر"
    
    upper1 = c1['high'] - c1['close'] if c1['close'] > c1['open'] else c1['high'] - c1['open']
    if upper1 > 2 * body1 and c1['high'] >= df['high'].rolling(10).max().iloc[-3] and c2['close'] < c2['open'] and c2['close'] < c1['low']:
        return "PUT", 25, "Shooting Star مؤكد + كسر"
    
    if abs(c1['low'] - c2['low']) / c1['low'] < 0.001 and c1['close'] < c1['open'] and c2['close'] > c2['open'] and c2['close'] > c1['high']:
        return "CALL", 26, "Tweezer Bottom مؤكد"
    
    if abs(c1['high'] - c2['high']) / c1['high'] < 0.001 and c1['close'] > c1['open'] and c2['close'] < c2['open'] and c2['close'] < c1['low']:
        return "PUT", 26, "Tweezer Top مؤكد"

    c1_body = abs(c1['close'] - c1['open'])
    c2_body = abs(c2['close'] - c2['open'])
    
    if c1['close'] < c1['open'] and c1_body > df['close'].rolling(10).std().iloc[-3] and c2_body < c1_body * 0.3 and c2['high'] < c1['low'] and c3['close'] > c3['open'] and c3['close'] > (c1['open'] + c1['close']) / 2:
        return "CALL", 35, "Morning Star مؤكد"
    
    if c1['close'] > c1['open'] and c1_body > df['close'].rolling(10).std().iloc[-3] and c2_body < c1_body * 0.3 and c2['low'] > c1['high'] and c3['close'] < c3['open'] and c3['close'] < (c1['open'] + c1['close']) / 2:
        return "PUT", 35, "Evening Star مؤكد"

    return None, 0, ""

def calc_ai_score(df):
    if len(df) < 50:
        return 0, None, ""
    
    close = df['close']
    volume = df['volume']
    
    ema9 = ta.trend.ema_indicator(close, 9).iloc[-1]
    ema21 = ta.trend.ema_indicator(close, 21).iloc[-1]
    rsi = ta.momentum.rsi(close, 14).iloc[-1]
    macd = ta.trend.macd_diff(close).iloc[-1]
    atr = ta.volatility.average_true_range(df['high'], df['low'], close, 14).iloc[-1]
    
    vol_avg = volume.rolling(20).mean().iloc[-1]
    vol_spike = volume.iloc[-1] > vol_avg * 1.5
    
    support = df['low'].rolling(20).min().iloc[-1]
    resistance = df['high'].rolling(20).max().iloc[-1]
    price = close.iloc[-1]
    
    score = 0
    reasons = []
    
    if ema9 > ema21:
        score += 15
        reasons.append("EMA صاعد")
    else:
        score -= 15
        reasons.append("EMA هابط")
    
    if rsi < 30:
        score += 20
        reasons.append("RSI تشبع بيعي")
    elif rsi > 70:
        score -= 20
        reasons.append("RSI تشبع شرائي")
    elif 40 < rsi < 60:
        score += 5
    
    if macd > 0:
        score += 15
        reasons.append("MACD ايجابي")
    else:
        score -= 15
        reasons.append("MACD سلبي")
    
    if vol_spike:
        score += 10
        reasons.append("فوليوم عالي")
    
    if abs(price - support) / price < 0.001:
        score += 15
        reasons.append("عند دعم")
    elif abs(price - resistance) / price < 0.001:
        score -= 15
        reasons.append("عند مقاومة")
    
    atr_pct = (atr / price) * 100
    if atr_pct > 0.15:
        score += 5
        reasons.append("تذبذب جيد")
    
    direction, candle_score, candle_name = check_confirmed_patterns(df)
    if direction:
        score += candle_score
        reasons.append(candle_name)
    
    return score, direction, " + ".join(reasons)

async def auto_scan(context: ContextTypes.DEFAULT_TYPE):
    for name, symbol in PAIRS.items():
        df = await get_candles(symbol)
        if df is None or len(df) < 50:
            continue
        
        score, direction, reasons = calc_ai_score(df)
        current_time = df.iloc[-1]['datetime']
        signal_key = f"{name}_{current_time}_{direction}"
        
        if abs(score) >= 60 and direction and signal_key not in last_signals:
            emoji = "⬆️" if direction == "CALL" else "⬇️"
            entry_time = (datetime.strptime(current_time, "%Y-%m-%d %H:%M:%S") + timedelta(minutes=1)).strftime("%H:%M")
            
            message = f"""🚨 **إشارة مؤكدة**

**الزوج:** {name} {emoji}
**الصفقة:** `{direction}`
**الدخول:** `{entry_time}` - مدة دقيقة
**القوة:** `{abs(score)}%`

**الأسباب:**
{reasons}

⚠️ ادخل الشمعة الجاية مباشرة"""
            
            await context.bot.send_message(chat_id=CHAT_ID, text=message, parse_mode='Markdown')
            last_signals[signal_key] = True
            if len(last_signals) > 50:
                last_signals.clear()
        
        await asyncio.sleep(2)

async def scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("🔍 جاري الفحص...")
    results = []
    
    for name, symbol in PAIRS.items():
        df = await get_candles(symbol)
        if df is None or len(df) < 50:
            continue
        
        score, direction, reasons = calc_ai_score(df)
        
        if abs(score) >= 60 and direction:
            emoji = "⬆️" if direction == "CALL" else "⬇️"
            results.append(f"**{name}** {emoji} `{direction}`\nقوة: `{abs(score)}%`\n{reasons}\n")
        
        await asyncio.sleep(1)
    
    if results:
        await msg.edit_text("**🚨 فرص قوية مؤكدة:**\n\n" + "\n".join(results), parse_mode='Markdown')
    else:
        await msg.edit_text("❌ لا توجد فرص مؤكدة حالياً")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("بوت اشارات الخيارات الثنائية جاهز ✅\nالفحص التلقائي شغال كل 5 دقايق\n\nارسل /scan للفحص اليدوي")

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("scan", scan))
    
    job_queue = app.job_queue
    job_queue.run_repeating(auto_scan, interval=300, first=10)
    
    app.run_polling()

if __name__ == "__main__":
    main()
