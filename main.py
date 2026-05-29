import os
import json
import asyncio
import requests
import pandas as pd
import ta
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler

TOKEN = os.getenv("TOKEN")
TWELVE_API = os.getenv("TWELVE_API_KEY")
CHAT_ID = os.getenv("CHAT_ID")
SETTINGS_FILE = "settings.json"

DEFAULT_SETTINGS = {
    "pairs": {
        "EUR/USD": "EUR/USD",
        "GBP/USD": "GBP/USD",
        "USD/JPY": "USD/JPY",
        "EUR/USD OTC": "EUR/USD:OTC",
        "GBP/USD OTC": "GBP/USD:OTC"
    },
    "min_score": 60,
    "interval": "1min",
    "use_rsi": True, "rsi_period": 14,
    "use_ema": True, "ema_fast": 9, "ema_slow": 21,
    "use_macd": True,
    "use_patterns": True,
    "auto_trade": True,
    "members": [],
    "signal_template": "🚨 **إشارة مؤكدة**\n\n**الزوج:** {pair} {emoji}\n**الصفقة:** `{direction}`\n**الدخول:** `{entry_time}` - مدة دقيقة\n**القوة:** `{score}%`\n\n**الأسباب:**\n{reasons}\n\n⚠️ ادخل الشمعة الجاية مباشرة",
    "strategy": "default"
}

last_signals = {}

def load_settings():
    try:
        with open(SETTINGS_FILE, 'r') as f:
            settings = json.load(f)
            for key, value in DEFAULT_SETTINGS.items():
                if key not in settings: settings[key] = value
            return settings
    except:
        return DEFAULT_SETTINGS.copy()

def save_settings(settings):
    with open(SETTINGS_FILE, 'w') as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)

def is_admin(user_id):
    return str(user_id) == str(CHAT_ID)

def is_member(user_id):
    s = load_settings()
    return is_admin(user_id) or str(user_id) in s["members"]

def format_signal(pair, direction, score, reasons, entry_time):
    s = load_settings()
    emoji = "⬆️" if direction == "CALL" else "⬇️"
    return s["signal_template"].format(
        pair=pair, emoji=emoji, direction=direction, score=abs(score),
        reasons=reasons, entry_time=entry_time
    )

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

def calc_ai_score(df, s):
    if len(df) < 50:
        return 0, None, ""

    close = df['close']
    volume = df['volume']
    score = 0
    reasons = []

    if s["use_ema"]:
        ema_fast = ta.trend.ema_indicator(close, s["ema_fast"]).iloc[-1]
        ema_slow = ta.trend.ema_indicator(close, s["ema_slow"]).iloc[-1]
        if ema_fast > ema_slow:
            score += 15
            reasons.append(f"EMA{s['ema_fast']}>{s['ema_slow']}")
        else:
            score -= 15
            reasons.append(f"EMA{s['ema_fast']}<{s['ema_slow']}")

    if s["use_rsi"]:
        rsi = ta.momentum.rsi(close, s["rsi_period"]).iloc[-1]
        if rsi < 30:
            score += 20
            reasons.append(f"RSI {rsi:.0f} بيع")
        elif rsi > 70:
            score -= 20
            reasons.append(f"RSI {rsi:.0f} شراء")
        elif 40 < rsi < 60:
            score += 5

    if s["use_macd"]:
        macd = ta.trend.macd_diff(close).iloc[-1]
        if macd > 0:
            score += 15
            reasons.append("MACD +")
        else:
            score -= 15
            reasons.append("MACD -")

    atr = ta.volatility.average_true_range(df['high'], df['low'], close, 14).iloc[-1]
    vol_avg = volume.rolling(20).mean().iloc[-1]
    vol_spike = volume.iloc[-1] > vol_avg * 1.5

    support = df['low'].rolling(20).min().iloc[-1]
    resistance = df['high'].rolling(20).max().iloc[-1]
    price = close.iloc[-1]

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

    direction, candle_score, candle_name = (None, 0, "")
    if s["use_patterns"]:
        direction, candle_score, candle_name = check_confirmed_patterns(df)
        if direction:
            score += candle_score
            reasons.append(candle_name)

    return score, direction, " + ".join(reasons)

async def auto_scan(context: ContextTypes.DEFAULT_TYPE):
    s = load_settings()
    if not s["auto_trade"]: return

    for name, symbol in s["pairs"].items():
        df = await get_candles(symbol, s["interval"])
        if df is None or len(df) < 50:
            continue

        score, direction, reasons = calc_ai_score(df, s)
        current_time = df.iloc[-1]['datetime']
        signal_key = f"{name}_{current_time}_{direction}"

        if abs(score) >= s["min_score"] and direction and signal_key not in last_signals:
            entry_time = (datetime.strptime(current_time, "%Y-%m-%d %H:%M:%S") + timedelta(minutes=1)).strftime("%H:%M")
            message = format_signal(name, direction, abs(score), reasons, entry_time)

            for uid in [CHAT_ID] + s["members"]:
                try: await context.bot.send_message(chat_id=uid, text=message, parse_mode='Markdown')
                except: pass

            last_signals[signal_key] = True
            if len(last_signals) > 50:
                last_signals.clear()

        await asyncio.sleep(2)

async def scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_member(update.effective_user.id):
        await update.message.reply_text("❌ ما عندك صلاحية")
        return

    s = load_settings()
    msg = await update.message.reply_text("🔍 جاري الفحص...")
    results = []

    for name, symbol in s["pairs"].items():
        df = await get_candles(symbol, s["interval"])
        if df is None or len(df) < 50:
            continue

        score, direction, reasons = calc_ai_score(df, s)

        if abs(score) >= s["min_score"] and direction:
            emoji = "⬆️" if direction == "CALL" else "⬇️"
            results.append(f"**{name}** {emoji} `{direction}`\nقوة: `{abs(score)}%`\n{reasons}\n")

        await asyncio.sleep(1)

    if results:
        await msg.edit_text("**🚨 فرص قوية مؤكدة:**\n\n" + "\n".join(results), parse_mode='Markdown')
    else:
        await msg.edit_text("❌ لا توجد فرص مؤكدة حالياً")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_member(update.effective_user.id):
        await update.message.reply_text("❌ ما عندك صلاحية. اطلب من الادمن يضيفك")
        return
    await update.message.reply_text("بوت اشارات الخيارات الثنائية جاهز ✅\nالفحص التلقائي شغال كل 5 دقايق\n\n/panel = لوحة التحكم\n/scan = فحص يدوي")

async def panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ اللوحة للادمن فقط")
        return
    s = load_settings()
    keyboard = [
        [InlineKeyboardButton("1️⃣ الازواج", callback_data="menu_pairs"),
         InlineKeyboardButton("2️⃣ الاعضاء", callback_data="menu_members")],
        [InlineKeyboardButton("3️⃣ رابط الدعوة", callback_data="menu_invite"),
         InlineKeyboardButton("4️⃣ الاستراتيجية", callback_data="menu_strategy")],
        [InlineKeyboardButton("5️⃣ المؤشرات", callback_data="menu_indicators"),
         InlineKeyboardButton("6️⃣ تلقائي " + ("✅" if s["auto_trade"] else "❌"), callback_data="menu_auto")],
        [InlineKeyboardButton("7️⃣ الفريم", callback_data="menu_interval"),
         InlineKeyboardButton("8️⃣ شكل الرسالة", callback_data="menu_template")]
    ]
    await update.message.reply_text("**🎛️ لوحة التحكم**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    s = load_settings()

    if query.data == "menu_pairs":
        pairs_list = '\n'.join([f"- {k}" for k in s['pairs'].keys()])
        text = f"**الازواج الحالية:**\n{pairs_list}\n\n/addpair EUR/USD OTC\n/removepair EUR/USD"
        await query.edit_message_text(text, parse_mode='Markdown')

    elif query.data == "menu_members":
        members = '\n'.join(s['members']) if s['members'] else "لا يوجد"
        text = f"**الاعضاء:**\n{members}\n\n/addmember 123456789\n/removemember 123456789"
        await query.edit_message_text(text)

    elif query.data == "menu_invite":
        bot_info = await context.bot.get_me()
        text = f"**رابط البوت:**\nhttps://t.me/{bot_info.username}"
        await query.edit_message_text(text)

    elif query.data == "menu_strategy":
        text = f"**اقل قوة:** {s['min_score']}%\n\n/setscore 70"
        await query.edit_message_text(text)

    elif query.data == "menu_indicators":
        keyboard = [
            [InlineKeyboardButton(f"RSI {'✅' if s['use_rsi'] else '❌'}", callback_data="toggle_rsi"),
             InlineKeyboardButton(f"EMA {'✅' if s['use_ema'] else '❌'}", callback_data="toggle_ema")],
            [InlineKeyboardButton(f"MACD {'✅' if s['use_macd'] else '❌'}", callback_data="toggle_macd"),
             InlineKeyboardButton(f"نماذج {'✅' if s['use_patterns'] else '❌'}", callback_data="toggle_patterns")],
            [InlineKeyboardButton("⬅️ رجوع", callback_data="back_panel")]
        ]
        await query.edit_message_text("**المؤشرات:**", reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data == "menu_auto":
        s["auto_trade"] = not s["auto_trade"]
        save_settings(s)
        await panel(update, context)

    elif query.data == "menu_interval":
        text = f"**الفريم:** {s['interval']}\n\n/setinterval 5min"
        await query.edit_message_text(text)

    elif query.data == "menu_template":
        text = f"**القالب:**\n`{s['signal_template']}`\n\n/settemplate النص"
        await query.edit_message_text(text)

    elif query.data.startswith("toggle_"):
        key = "use_" + query.data.split("_")[1]
        s[key] = not s[key]
        save_settings(s)
        await button_handler(update, context)

    elif query.data == "back_panel":
        await panel(update, context)

async def addpair(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not context.args: await update.message.reply_text("مثال: /addpair EUR/USD OTC"); return
    s = load_settings()
    name = " ".join(context.args)
    symbol = name.replace(" OTC", ":OTC")
    s["pairs"][name] = symbol
    save_settings(s)
    await update.message.reply_text(f"تم اضافة {name} ✅")

async def removepair(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not context.args: await update.message.reply_text("مثال: /removepair EUR/USD"); return
    s = load_settings()
    name = " ".join(context.args)
    if name in s["pairs"]:
        del s["pairs"][name]
        save_settings(s)
    await update.message.reply_text(f"تم حذف {name} ✅")

async def addmember(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not context.args: await update.message.reply_text("مثال: /addmember 123456789"); return
    s = load_settings(); uid = context.args[0]
    if uid not in s["members"]: s["members"].append(uid); save_settings(s)
    await update.message.reply_text(f"تم اضافة العضو {uid} ✅")

async def removemember(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not context.args: await update.message.reply_text("مثال: /removemember 123456789"); return
    s = load_settings(); uid = context.args[0]
    if uid in s["members"]: s["members"].remove(uid); save_settings(s)
    await update.message.reply_text(f"تم حذف العضو {uid} ✅")

async def setscore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    s = load_settings(); s["min_score"] = int(context.args[0]); save_settings(s)
    await update.message.reply_text(f"اقل قوة: {s['min_score']}% ✅")

async def setinterval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    s = load_settings(); s["interval"] = context.args[0]; save_settings(s)
    await update.message.reply_text(f"الفريم: {s['interval']} ✅")

async def settemplate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    s = load_settings(); s["signal_template"] = " ".join(context.args); save_settings(s)
    await update.message.reply_text("تم تحديث القالب ✅")

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("scan", scan))
    app.add_handler(CommandHandler("panel", panel))
    app.add_handler(CommandHandler("addpair", addpair))
    app.add_handler(CommandHandler("removepair", removepair))
    app.add_handler(CommandHandler("addmember", addmember))
    app.add_handler(CommandHandler("removemember", removemember))
    app.add_handler(CommandHandler("setscore", setscore))
    app.add_handler(CommandHandler("setinterval", setinterval))
    app.add_handler(CommandHandler("settemplate", settemplate))
    app.add_handler(CallbackQueryHandler(button_handler))

    job_queue = app.job_queue
    job_queue.run_repeating(auto_scan, interval=300, first=10)

    app.run_polling()

if __name__ == "__main__":
    main()
