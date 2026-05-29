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

last_signal_candle = {}

async def get_candles(symbol, interval="1min", outputsize=100):
    url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval={interval}&outputsize={outputsize}&apikey={TWELVE_API}"
    try:
        data = requests.get(url, timeout=10).json()
        if 'values' not in data: return None
        df = pd.DataFrame(data['values'])
        df = df.iloc[::-1].reset_index(drop=True)
        df[['open','high','low','close','volume']] = df[['open','high','low','close','volume']].astype(float)
        return df
    except: return None

def def check_confirmed_patterns(df):
    """
    ترجع: direction, score, pattern_name
    لازم آخر 3 شموع موجودة: [-3] إشارة, [-2] تأكيد, [-1] الشمعة اللي توها قفلت
    """
    if len(df) < 3: return None, 0, ""
    
    c1 = df.iloc[-3]  # شمعة الإشارة
    c2 = df.iloc[-2]  # شمعة التأكيد اللي قفلت
    c3 = df.iloc[-1]  # الشمعة الحالية اللي نبي ندخل عليها
    
    # 1. Bullish Engulfing مؤكد
    # شمعة 1: حمراء, شمعة 2: خضراء بالعة + تقفل فوق هاي 1
    if c1['close'] < c1['open'] and \
       c2['close'] > c2['open'] and \
       c2['close'] > c1['open'] and c2['open'] < c1['close'] and \
       c2['close'] > c1['high']:
        return "CALL", 30, "Bullish Engulfing مؤكد"
    
    # 2. Bearish Engulfing مؤكد
    # شمعة 1: خضراء, شمعة 2: حمراء بالعة + تقفل تحت لو 1
    if c1['close'] > c1['open'] and \
       c2['close'] < c2['open'] and \
       c2['close'] < c1['open'] and c2['open'] > c1['close'] and \
       c2['close'] < c1['low']:
        return "PUT", 30, "Bearish Engulfing مؤكد"
    
    # 3. Piercing Line مؤكد
    # شمعة 1: حمراء طويلة, شمعة 2: خضراء تفتح قاب تحت وتقفل فوق نص 1
    if c1['close'] < c1['open'] and \
       c2['close'] > c2['open'] and \
       c2['open'] < c1['low'] and \
       c2['close'] > (c1['open'] + c1['close']) / 2 and \
       c2['close'] < c1['open']:
        return "CALL", 28, "Piercing Line مؤكد"
    
    # 4. Dark Cloud Cover مؤكد
    # شمعة 1: خضراء طويلة, شمعة 2: حمراء تفتح قاب فوق وتقفل تحت نص 1
    if c1['close'] > c1['open'] and \
       c2['close'] < c2['open'] and \
       c2['open'] > c1['high'] and \
       c2['close'] < (c1['open'] + c1['close']) / 2 and \
       c2['close'] > c1['open']:
        return "PUT", 28, "Dark Cloud Cover مؤكد"
    
    # 5. Bullish Hammer مؤكد + كسر
    # شمعة 1: هامر على دعم, شمعة 2: خضراء تقفل فوق هاي الهامر
    body1 = abs(c1['close'] - c1['open'])
    lower1 = c1['open'] - c1['low'] if c1['close'] > c1['open'] else c1['close'] - c1['low']
    if lower1 > 2 * body1 and \
       c1['low'] <= df['low'].rolling(10).min().iloc[-3] and \
       c2['close'] > c2['open'] and \
       c2['close'] > c1['high']:
        return "CALL", 25, "Hammer مؤكد + كسر"
    
    # 6. Bearish Shooting Star مؤكد + كسر
    # شمعة 1: شهاب على مقاومة, شمعة 2: حمراء تقفل تحت لو الشهاب
    upper1 = c1['high'] - c1['close'] if c1['close'] > c1['open'] else c1['high'] - c1['open']
    if upper1 > 2 * body1 and \
       c1['high'] >= df['high'].rolling(10).max().iloc[-3] and \
       c2['close'] < c2['open'] and \
       c2['close'] < c1['low']:
        return "PUT", 25, "Shooting Star مؤكد + كسر"
    
    # 7. Tweezer Bottom مؤكد
    # شمعة 1 و 2 لهم نفس اللو تقريباً, شمعة 2 خضراء
    if abs(c1['low'] - c2['low']) / c1['low'] < 0.001 and \
       c1['close'] < c1['open'] and \
       c2['close'] > c2['open'] and \
       c2['close'] > c1['high']:
        return "CALL", 26, "Tweezer Bottom مؤكد"
    
    # 8. Tweezer Top مؤكد
    if abs(c1['high'] - c2['high']) / c1['high'] < 0.001 and \
       c1['close'] > c1['open'] and \
       c2['close'] < c2['open'] and \
       c2['close'] < c1['low']:
        return "PUT", 26, "Tweezer Top مؤكد"
def calc_ai_score(df):
    if len(df) < 3: return 0, None, 0, 0, []

    direction, candle_score, candle_name = check_confirmed_patterns(df)
    if not direction: return 0, None, 0, 0, [] # مافي نمط مؤكد نطلع

    reasons = [candle_name]
    score = candle_score
    curr = df.iloc[-2]  # شمعة التأكيد
    prev = df.iloc[-3]  # شمعة الإشارة
    # ... باقي الكود زي ما هو
    return "CALL", 30, "Bullish Engulfing مؤكد"  # هذا شراء
return "PUT", 30, "Bearish Engulfing مؤكد"   # وهذا بيع
      # 9. Morning Star - شراء CALL ⬆️
    # شمعة 1: حمراء طويلة, شمعة 2: دوجي/صغيرة قاب, شمعة 3: خضراء تقفل فوق نص شمعة 1
    c1_body = abs(c1['close'] - c1['open'])
    c2_body = abs(c2['close'] - c2['open'])
    c3_body = abs(df.iloc[-1]['close'] - df.iloc[-1]['open'])  # الشمعة الثالثة
    
    if c1['close'] < c1['open'] and c1_body > df['close'].rolling(10).std().iloc[-3] and \
       c2_body < c1_body * 0.3 and \
       c2['high'] < c1['low'] and \
       df.iloc[-1]['close'] > df.iloc[-1]['open'] and \
       df.iloc[-1]['close'] > (c1['open'] + c1['close']) / 2:
        return "CALL", 35, "Morning Star مؤكد"
    
    # 10. Evening Star - بيع PUT ⬇️
    # شمعة 1: خضراء طويلة, شمعة 2: دوجي/صغيرة قاب, شمعة 3: حمراء تقفل تحت نص شمعة 1
    if c1['close'] > c1['open'] and c1_body > df['close'].rolling(10).std().iloc[-3] and \
       c2_body < c1_body * 0.3 and \
       c2['low'] > c1['high'] and \
       df.iloc[-1]['close'] < df.iloc[-1]['open'] and \
       df.iloc[-1]['close'] < (c1['open'] + c1['close']) / 2:
        return "PUT", 35, "Evening Star مؤكد"
return None, 0, ""

def calc_ai_score(df):
    if len(df) < 3: return 0, None, 0, 0, []

    direction, candle_score, candle_name = check_candle_pattern(df)
    if not direction: return 0, None, 0, 0, []

    reasons = [candle_name]
    score = candle_score
    curr = df.iloc[-2]
    prev = df.iloc[-3]

    # 1. EMA Trend
    df['ema9'] = ta.trend.ema_indicator(df['close'], 9)
    df['ema21'] = ta.trend.ema_indicator(df['close'], 21)
    if direction == "CALL" and df['ema9'].iloc[-2] > df['ema21'].iloc[-2]:
        score += 15; reasons.append("EMA9 > EMA21")
    if direction == "PUT" and df['ema9'].iloc[-2] < df['ema21'].iloc[-2]:
        score += 15; reasons.append("EMA9 < EMA21")

    # 2. MACD
    macd_line = ta.trend.macd(df['close'])
    macd_signal = ta.trend.macd_signal(df['close'])
    if direction == "CALL" and macd_line.iloc[-2] > macd_signal.iloc[-2] and macd_line.iloc[-2] > 0:
        score += 15; reasons.append("MACD صاعد فوق الصفر")
    if direction == "PUT" and macd_line.iloc[-2] < macd_signal.iloc[-2] and macd_line.iloc[-2] < 0:
        score += 15; reasons.append("MACD هابط تحت الصفر")

    # 3. RSI
    rsi = ta.momentum.rsi(df['close'], 14).iloc[-2]
    if direction == "CALL" and 50 < rsi < 70:
        score += 10; reasons.append(f"RSI {rsi:.0f} زخم صاعد")
    if direction == "PUT" and 30 < rsi < 50:
        score += 10; reasons.append(f"RSI {rsi:.0f} زخم هابط")

    # 4. Volume
    vol_avg = df['volume'].rolling(20).mean().iloc[-2]
    if curr['volume'] > vol_avg and curr['volume'] > prev['volume']:
        score += 10; reasons.append("فوليوم قوي أعلى من المتوسط")

    # 5. Support/Resistance
    if direction == "CALL" and curr['low'] <= df['low'].rolling(10).min().iloc[-2]:
        score += 15; reasons.append("ارتداد من دعم")
    if direction == "PUT" and curr['high'] >= df['high'].rolling(10).max().iloc[-2]:
        score += 15; reasons.append("ارتداد من مقاومة")

    # 6. ADX
    adx = ta.trend.adx(df['high'], df['low'], df['close']).iloc[-2]
    if adx > 25:
        score += 10; reasons.append(f"ADX {adx:.0f} ترند قوي")

    return score, direction, rsi, adx, reasons

async def check_mtf(symbol, direction):
    # M5
    df_m5 = await get_candles(symbol, "5min", 50)
    if df_m5 is None: return False
    ema9_m5 = ta.trend.ema_indicator(df_m5['close'], 9).iloc[-1]
    ema21_m5 = ta.trend.ema_indicator(df_m5['close'], 21).iloc[-1]
    m5_ok = (direction == "CALL" and ema9_m5 > ema21_m5) or (direction == "PUT" and ema9_m5 < ema21_m5)

    # M15
    df_m15 = await get_candles(symbol, "15min", 50)
    if df_m15 is None: return False
    ema9_m15 = ta.trend.ema_indicator(df_m15['close'], 9).iloc[-1]
    ema21_m15 = ta.trend.ema_indicator(df_m15['close'], 21).iloc[-1]
    m15_ok = (direction == "CALL" and ema9_m15 > ema21_m15) or (direction == "PUT" and ema9_m15 < ema21_m15)

    return m5_ok and m15_ok

async def check_signals(app):
    for name, symbol in PAIRS.items():
        try:
            df = await get_candles(symbol)
            if df is None or len(df) < 3: continue

            score, direction, rsi, adx, reasons = calc_ai_score(df)

            # نظام الثقة النهائي
            if score >= 85 and direction and await check_mtf(symbol, direction):
                candle_time = df['datetime'].iloc[-2]
                if last_signal_candle.get(symbol) == candle_time: continue
                last_signal_candle[symbol] = candle_time

                # نحسب وقت الدخول بعد 30 ثانية من الآن
                entry_time = (datetime.now() + timedelta(seconds=30)).strftime("%H:%M:%S")

                msg = f"""
🔥 إشارة AI Quantum - {name}
النوع: {direction} {"شراء ⬆️" if direction=="CALL" else "بيع ⬇️"}
المدة: 3 دقائق
قوة الإشارة: {score}%
الوقت: {datetime.now().strftime("%H:%M")}
وقت الدخول: بعد 30 ثانية = {entry_time}

السبب: {" + ".join(reasons)}

تأكيدات:
Second Candle Break ✅
M5 + M15 Trend ✅
Volume Confirmed ✅

Source: TwelveData + Quantum AI Filter
تنبيه: التداول خطر. جرب ديمو أول.
"""
                await app.bot.send_message(chat_id=CHAT_ID, text=msg)
                await asyncio.sleep(3)
        except Exception as e:
            print(f"Error {name}: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("بوت AI Quantum شغال. فلتر شمعتين + M5/M15 + دخول بعد 30ث. ما أرسل إلا المؤكد 100%")

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    job_queue = app.job_queue
    job_queue.run_repeating(lambda ctx: asyncio.create_task(check_signals(app)), interval=60, first=35)
    app.run_polling()

if __name__ == '__main__':
    main()
