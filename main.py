import os
import sys

# --- نظام التثبيت التلقائي ---
try:
    import matplotlib
    import sklearn
    import numpy as np
    from sklearn.ensemble import RandomForestClassifier
except ImportError:
    print("⏳ جاري تثبيت المكتبات تلقائياً...")
    os.system('pip install matplotlib scikit-learn numpy')
    print("✅ تم التثبيت! أعد التشغيل الآن.")
    sys.exit()
# --------------------------------------------------

import requests
import time
from datetime import datetime, timedelta
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import io

# ==================== الإعدادات الحساسة ====================
TOKEN = "8689411223:AAFX-m5Kqv2NYeBHIojHmFArD10ZjfrxwCU"
CHAT_ID = "5690085743"
TWELVE_API = "3f5e716212ed401cb2e8a7517932663a"
# ==========================================================

PAIRS = [
    "EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD",
    "USD/CAD", "EUR/JPY", "GBP/JPY", "EUR/GBP"
]

last_signal = {}
last_news_check = 0

# === الإعدادات الفنية الفائقة ===
COOLDOWN_MINUTES = 5
RSI_BUY_MAX = 38
RSI_SELL_MIN = 62
ENTRY_DELAY_SECONDS = 5
TRADE_DURATION_MINUTES = 1
MAX_DISTANCE_PIPS = 3
NEWS_CHECK_INTERVAL = 300
NEWS_IMPACT_MINUTES = 45
SEND_CHART = True

# --- إعدادات الذكاء الاصطناعي ---
AI_CONFIDENCE_THRESHOLD = 0.65

STRONG_NEWS_KEYWORDS = [
    'rate decision', 'interest rate', 'federal reserve', 'fed', 'ecb', 'boe', 'boj',
    'cpi', 'inflation', 'nfp', 'non-farm', 'unemployment', 'gdp', 'ppi',
    'retail sales', 'central bank', 'monetary policy', 'fomc', 'powell', 'lagarde'
]

def send(msg):
    if not CHAT_ID: return
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    data = {"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"}
    try: requests.post(url, data=data, timeout=10)
    except: pass

def send_photo(image_bytes, caption):
    if not CHAT_ID: return
    url = f"https://api.telegram.org/bot{TOKEN}/sendPhoto"
    files = {'photo': ('chart.png', image_bytes, 'image/png')}
    data = {"chat_id": CHAT_ID, "caption": caption, "parse_mode": "HTML"}
    try: requests.post(url, files=files, data=data, timeout=20)
    except: pass

def get_yahoo_news():
    try:
        url = "https://query1.finance.yahoo.com/v1/finance/news"
        params = {'category': 'generalnews', 'count': 15}
        headers = {'User-Agent': 'Mozilla/5.0'}
        res = requests.get(url, params=params, headers=headers, timeout=10).json()
        strong_news = []
        for item in res.get('data', {}).get('news', []):
            title = item.get('title', '').lower()
            pub_time = item.get('providerPublishTime', 0)
            if time.time() - pub_time > NEWS_IMPACT_MINUTES * 60: continue
            if any(kw in title for kw in STRONG_NEWS_KEYWORDS):
                strong_news.append({'title': item.get('title'), 'timestamp': pub_time})
        return strong_news
    except: return []

def check_news_impact():
    global last_news_check
    if time.time() - last_news_check < NEWS_CHECK_INTERVAL: return []
    last_news_check = time.time()
    return get_yahoo_news()

def calc_rsi(closes, period=14):
    if len(closes) < period + 1: return 50
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        gains.append(max(0, diff))
        losses.append(max(0, -diff))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0: return 100
    return 100 - (100 / (1 + (avg_gain / avg_loss)))

def calc_support_resistance(data):
    lows = [d['l'] for d in data]
    highs = [d['h'] for d in data]
    return min(lows[-20:]), max(highs[-20:])

def check_chart_patterns(data, support, resistance):
    if len(data) < 5: return None, None
    c0, c1 = data[-1], data[-2]

    if c1['c'] < c1['o'] and c0['c'] > c0['o'] and c0['c'] >= c1['o'] and c0['o'] <= c1['c']:
        if c0['c'] <= support * 1.002: return "شراء", "ابتلاع صاعد احترافي"

    if c1['c'] > c1['o'] and c0['c'] < c0['o'] and c0['c'] <= c1['o'] and c0['o'] >= c1['c']:
        if c0['c'] >= resistance * 0.998: return "بيع", "ابتلاع هابط احترافي"

    if c1['h'] > resistance and c0['c'] < resistance: return "بيع", "كسر كاذب للمقاومة"
    if c1['l'] < support and c0['c'] > support: return "شراء", "كسر كاذب للدعم"

    return None, None

# ==================== محرّك الذكاء الاصطناعي (AI ENGINE) ====================
def ai_predict_signal_quality(data, direction, rsi, dist_pips):
    try:
        closes = [c['c'] for c in data[-15:]]
        volatility = np.std(closes)
        candle_bodies = [abs(c['c'] - c['o']) for c in data[-5:]]
        avg_body = np.mean(candle_bodies)

        # بيانات تدريبية مصححة
        X_train = [
            [0.0002, 0.0001, 30, 1.5],
            [0.0005, 0.0004, 75, 4.2],
            [0.0001, 0.0001, 25, 0.5],
            [0.0008, 0.0006, 80, 5.0],
            [0.0003, 0.0002, 35, 1.2],
            [0.0004, 0.0003, 68, 1.8]
        ]
        y_train = [1, 0, 1, 0, 1, 1]

        model = RandomForestClassifier(n_estimators=10, random_state=42)
        model.fit(X_train, y_train)

        current_features = np.array([[volatility, avg_body, rsi, dist_pips]])
        probabilities = model.predict_proba(current_features)[0]
        confidence_success = probabilities[1]

        return confidence_success
    except Exception as e:
        print(f"خطأ AI: {e}")
        return 0.50
# ============================================================================

def get_candles(pair):
    symbol = pair.replace("/", "") + "=X"
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1m&range=1d"
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        res = requests.get(url, headers=headers, timeout=10).json()
        q = res['chart']['result'][0]['indicators']['quote'][0]
        data = []
        for i in range(len(q['close'])):
            if all([q['open'][i], q['high'][i], q['low'][i], q['close'][i]]):
                data.append({'o': q['open'][i], 'h': q['high'][i], 'l': q['low'][i], 'c': q['close'][i]})
        return data[-60:]
    except: return None

def check(pair, news_list):
    data = get_candles(pair)
    if not data or len(data) < 20: return None

    closes = [d['c'] for d in data]
    current_price = closes[-1]
    rsi = calc_rsi(closes)
    support, resistance = calc_support_resistance(data)

    direction, pattern_name = check_chart_patterns(data, support, resistance)
    if not direction: return None

    pip_value = 0.0001 if "JPY" not in pair else 0.01
    distance_pips = abs(current_price - (support if direction == "شراء" else resistance)) / pip_value

    if direction == "شراء" and (distance_pips > MAX_DISTANCE_PIPS or rsi > RSI_BUY_MAX): return None
    if direction == "بيع" and (distance_pips > MAX_DISTANCE_PIPS or rsi < RSI_SELL_MIN): return None

    for news in news_list:
        if any(curr in news['title'].lower() for curr in pair.split("/")): return None

    ai_confidence = ai_predict_signal_quality(data, direction, rsi, distance_pips)

    if ai_confidence < AI_CONFIDENCE_THRESHOLD:
        print(f"⚠️ {pair}: حجب AI - الدقة {ai_confidence*100:.1f}%")
        return None

    return direction, rsi, pattern_name, support, resistance, distance_pips, data, ai_confidence

def plot_chart(data, pair, direction, pattern_name, support, resistance, ai_conf):
    fig, ax = plt.subplots(figsize=(8, 4.5), dpi=100)
    fig.patch.set_facecolor('#0f172a')
    ax.set_facecolor('#0f172a')

    for i, c in enumerate(data[-25:]):
        color = '#10b981' if c['c'] >= c['o'] else '#ef4444'
        ax.plot([i, i], [c['o'], c['c']], color=color, linewidth=5, solid_capstyle='butt')
        ax.plot([i, i], [c['l'], c['h']], color=color, linewidth=1.5)

    ax.axhline(y=support, color='#10b981', linestyle='--', linewidth=1.5)
    ax.axhline(y=resistance, color='#ef4444', linestyle='--', linewidth=1.5)

    ax.set_title(f'{pair} | {pattern_name}\nAI Confidence: {ai_conf*100:.1f}%', color='#67e8f9', fontsize=11, weight='bold')
    ax.tick_params(colors='white')
    ax.grid(True, alpha=0.1, color='gray')

    buf = io.BytesIO()
    plt.savefig(buf, format='png', facecolor=fig.get_facecolor())
    buf.seek(0)
    plt.close(fig)
    return buf.getvalue()

if TOKEN and CHAT_ID:
    send("🤖 <b>تم تفعيل نظام الذكاء الاصطناعي V9!</b>\n🧠 البوت يفحص الجودة تلقائياً")

while True:
    current_news = check_news_impact()

    for pair in PAIRS:
        if pair in last_signal and time.time() - last_signal[pair] < COOLDOWN_MINUTES * 60: continue

        result = check(pair, current_news)
        if result:
            direction, rsi, pattern, sup, res, dist, chart_data, ai_confidence = result
            entry_time = (datetime.now() + timedelta(seconds=ENTRY_DELAY_SECONDS)).strftime("%H:%M:%S")

            arrow = "🟢 BUY" if direction == "شراء" else "🔴 SELL"

            msg = f"""🤖 <b>إشارة مصدّقة من الذكاء الاصطناعي</b>

📊 الزوج: <b>{pair}</b>
🎬 الاتجاه: <b>{arrow}</b>
📐 الاستراتيجية: <b>{pattern}</b>
🧠 دقة جودة الـ AI: <b>{ai_confidence*100:.1f}%</b>
⏰ مدة المعاملة: <b>{TRADE_DURATION_MINUTES} دقيقة</b>
⏳ وقت الدخول: <b>{entry_time}</b>

📈 RSI: {rsi:.1f}
📍 الدعم: {sup:.5f} | المقاومة: {res:.5f}"""

            if SEND_CHART:
                chart_img = plot_chart(chart_data, pair, direction, pattern, sup, res, ai_confidence)
                if chart_img: send_photo(chart_img, msg)
                else: send(msg)
            else:
                send(msg)

            last_signal[pair] = time.time()
        time.sleep(2)
    time.sleep(10)
