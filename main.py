import os
import json
import asyncio
import requests
import pandas as pd
import ta
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler
import requests
import time
from datetime import datetime, timedelta
import requests
import time
from datetime import datetime, timedelta
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import io

TOKEN = "8689411223:AAFX-m5Kqv2NYeBHIojHmFArD10ZjfrxwCU"
TWELVE_API = "dcbae2fd3bc74b97b5afece09ca7abfa" # مفتاح TwelveData

PAIRS = [
    "EUR/USD OTC", "GBP/USD OTC", "USD/JPY OTC", "AUD/USD OTC",
    "USD/CAD OTC", "EUR/JPY OTC", "GBP/JPY OTC", "EUR/GBP OTC",
    "AUD/JPY OTC", "NZD/USD OTC", "USD/CHF OTC", "EUR/AUD OTC"
]

last_signal = {}
last_news_check = 0

# === الإعدادات ===
COOLDOWN_MINUTES = 3
RSI_BUY_MAX = 75
RSI_SELL_MIN = 25
ENTRY_DELAY_SECONDS = 60
TRADE_DURATION_MINUTES = 1
MIN_PATTERN_STRENGTH = 0.15
MAX_DISTANCE_PIPS = 5
PATTERN_LOOKBACK = 30
SEND_CHART = True
NEWS_CHECK_INTERVAL = 300 # كل 5 دقايق يشيك الاخبار
NEWS_IMPACT_MINUTES = 30 # الاخبار اللي اقل من 30 دقيقة تعتبر حديثة
# ===================

session_count = 0
SUMMARY_EVERY_SESSIONS = 6

summary = {
    "total_signals": 0, "buy_signals": 0, "sell_signals": 0,
    "strong_signals": 0, "medium_signals": 0, "weak_signals": 0,
    "active_pairs": {}, "active_members": set(), "yesterday_signals": 0,
    "news_signals": 0
}

# كلمات الاخبار القوية جداً فقط
STRONG_NEWS_KEYWORDS = [
    'rate decision', 'interest rate', 'federal reserve', 'fed', 'ecb', 'boe', 'boj',
    'cpi', 'inflation', 'nfp', 'non-farm', 'unemployment', 'gdp', 'ppi',
    'retail sales', 'central bank', 'monetary policy', 'fomc', 'powell', 'lagarde'
]

def get_chat_id():
    url = f"https://api.telegram.org/bot{TOKEN}/getUpdates"
    try:
        res = requests.get(url, timeout=10).json()
        if res["ok"] and res["result"]:
            return res["result"][-1]["message"]["chat"]["id"]
    except: return None

CHAT_ID = get_chat_id()

def send(msg):
    if not CHAT_ID: return
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    data = {"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"}
    requests.post(url, data=data)

def send_photo(image_bytes, caption):
    if not CHAT_ID: return
    url = f"https://api.telegram.org/bot{TOKEN}/sendPhoto"
    files = {'photo': ('chart.png', image_bytes, 'image/png')}
    data = {"chat_id": CHAT_ID, "caption": caption, "parse_mode": "HTML"}
    try:
        requests.post(url, files=files, data=data, timeout=20)
    except Exception as e:
        print(f"خطأ ارسال الصورة: {e}")

def get_yahoo_news():
    """يجيب الاخبار القوية من Yahoo Finance فقط"""
    try:
        url = "https://query1.finance.yahoo.com/v1/finance/news"
        params = {'category': 'generalnews', 'count': 20}
        headers = {'User-Agent': 'Mozilla/5.0'}
        res = requests.get(url, params=params, headers=headers, timeout=10).json()
        strong_news = []

        for item in res.get('data', {}).get('news', []):
            title = item.get('title', '').lower()
            summary = item.get('summary', '').lower()
            pub_time = item.get('providerPublishTime', 0)

            if time.time() - pub_time > NEWS_IMPACT_MINUTES * 60:
                continue

            is_strong = any(kw in title or kw in summary for kw in STRONG_NEWS_KEYWORDS)
            if is_strong:
                strong_news.append({
                    'title': item.get('title'),
                    'source': 'Yahoo Finance',
                    'time': datetime.fromtimestamp(pub_time).strftime("%H:%M"),
                    'url': item.get('link', ''),
                    'timestamp': pub_time
                })

        return strong_news
    except Exception as e:
        print(f"خطأ Yahoo News: {e}")
        return []

def get_twelvedata_news():
    """يجيب الاخبار القوية من TwelveData فقط"""
    if not TWELVE_API or TWELVE_API == "ضع_مفتاحك_هنا": return []
    try:
        url = f"https://api.twelvedata.com/news"
        params = {'apikey': TWELVE_API, 'source': 'all', 'language': 'en'}
        res = requests.get(url, params=params, timeout=10).json()
        strong_news = []

        for item in res.get('news', [])[:20]:
            title = item.get('title', '').lower()
            summary = item.get('summary', '').lower()
            pub_time_str = item.get('published_date', '')
            try:
                pub_time = datetime.fromisoformat(pub_time_str.replace('Z', '+00:00')).timestamp()
            except:
                continue

            if time.time() - pub_time > NEWS_IMPACT_MINUTES * 60:
                continue

            is_strong = any(kw in title or kw in summary for kw in STRONG_NEWS_KEYWORDS)
            if is_strong:
                strong_news.append({
                    'title': item.get('title'),
                    'source': 'TwelveData',
                    'time': datetime.fromtimestamp(pub_time).strftime("%H:%M"),
                    'url': item.get('url', ''),
                    'timestamp': pub_time
                })

        return strong_news
    except Exception as e:
        print(f"خطأ TwelveData News: {e}")
        return []

def check_news_impact():
    """يشيك الاخبار القوية ويرجعها - ما يرسل عشوائي"""
    global last_news_check
    if time.time() - last_news_check < NEWS_CHECK_INTERVAL:
        return []

    last_news_check = time.time()
    all_news = []
    all_news.extend(get_yahoo_news())
    all_news.extend(get_twelvedata_news())

    seen = set()
    unique_news = []
    for news in sorted(all_news, key=lambda x: x['timestamp'], reverse=True):
        if news['title'] not in seen:
            seen.add(news['title'])
            unique_news.append(news)

    if unique_news:
        print(f"📰 اخبار قوية مؤكدة: {len(unique_news)}")
        for news in unique_news:
            print(f" {news['source']}: {news['title'][:60]}")

    return unique_news

def plot_chart(data, pair, direction, pattern_name, support, resistance, news_title=None):
    if len(data) < 20: return None
    fig, ax = plt.subplots(figsize=(10, 6), dpi=100)
    fig.patch.set_facecolor('#0e1117')
    ax.set_facecolor('#0e1117')

    for i, c in enumerate(data[-30:]):
        color = '#26a69a' if c['c'] >= c['o'] else '#ef5350'
        ax.plot([i, i], [c['o'], c['c']], color=color, linewidth=4, solid_capstyle='butt')
        ax.plot([i, i], [c['l'], c['h']], color=color, linewidth=1)

    ax.axhline(y=support, color='#26a69a', linestyle='--', linewidth=2, alpha=0.8)
    ax.text(len(data[-30:])-1, support, f' دعم {support:.5f}', color='#26a69a', fontsize=9, va='center')

    ax.axhline(y=resistance, color='#ef5350', linestyle='--', linewidth=2, alpha=0.8)
    ax.text(len(data[-30:])-1, resistance, f' مقاومة {resistance:.5f}', color='#ef5350', fontsize=9, va='center')

    entry_idx = len(data[-30:]) - 1
    entry_price = data[-1]['c']
    if direction == "شراء":
        ax.annotate('⬆️ دخول', xy=(entry_idx, entry_price), xytext=(entry_idx-3, entry_price*0.999),
                    color='#26a69a', fontsize=12, weight='bold',
                    arrowprops=dict(arrowstyle='->', color='#26a69a', lw=2))
    else:
        ax.annotate('⬇️ دخول', xy=(entry_idx, entry_price), xytext=(entry_idx-3, entry_price*1.001),
                    color='#ef5350', fontsize=12, weight='bold',
                    arrowprops=dict(arrowstyle='->', color='#ef5350', lw=2))

    title = f'{pair} | {pattern_name} | {direction}'
    if news_title:
        title += f'\n📰 {news_title[:40]}'

    ax.set_title(title, color='white', fontsize=12, weight='bold')
    ax.tick_params(colors='white')
    ax.grid(True, alpha=0.2, color='gray')
    ax.spines['bottom'].set_color('white')
    ax.spines['left'].set_color('white')
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)

    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format='png', facecolor=fig.get_facecolor())
    buf.seek(0)
    plt.close(fig)
    return buf.getvalue()

def update_summary(direction, pair, strength, member_id, has_news=False):
    summary["total_signals"] += 1
    if has_news: summary["news_signals"] += 1
    if direction == "شراء": summary["buy_signals"] += 1
    else: summary["sell_signals"] += 1
    if strength == "قوية": summary["strong_signals"] += 1
    elif strength == "متوسطة": summary["medium_signals"] += 1
    else: summary["weak_signals"] += 1
    if pair not in summary["active_pairs"]: summary["active_pairs"] = 0
    summary["active_pairs"] += 1
    summary["active_members"].add(member_id)

def send_summary():
    if summary["total_signals"] == 0: return
    diff = summary["total_signals"] - summary["yesterday_signals"]
    top_pairs = sorted(summary["active_pairs"].items(), key=lambda x: x[1], reverse=True)[:3]
    msg = f"""📋 ملخص الأداء - آخر {SUMMARY_EVERY_SESSIONS} جلسات
📅 {datetime.now().strftime("%A، %d %B %Y")}
🕐 {datetime.now().strftime("%H:%M")}
━━━━━━━━━━━━━━━━━━━━━━
📊 الإجمالي: {summary["total_signals"]} {'↑' if diff > 0 else '↓'} {abs(diff)}
📰 اشارات اخبارية: {summary["news_signals"]}
🟢 شراء: {summary["buy_signals"]} | 🔴 بيع: {summary["sell_signals"]}
⚡ القوة: 🔥 {summary["strong_signals"]} | 🟡 {summary["medium_signals"]} | ⚪ {summary["weak_signals"]}
🏆 الأكثر نشاطاً:
"""
    for i, (pair, count) in enumerate(top_pairs):
        medal = ['🥇', '🥈', '🥉'][i]
        msg += f"{medal} {pair} — {count}\n"
    msg += f"━━━━━━━━━━━━━━━━━━━━━━\n👥 أعضاء: {len(summary['active_members'])} | أزواج: {len(summary['active_pairs'])}"
    send(msg)
    summary.update({"yesterday_signals": summary["total_signals"], "total_signals": 0,
                   "buy_signals": 0, "sell_signals": 0, "strong_signals": 0,
                   "medium_signals": 0, "weak_signals": 0, "news_signals": 0,
                   "active_pairs": {}, "active_members": set()})

def get_candles(pair):
    symbol = pair.replace(" OTC", "").replace("/", "") + "=X"
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1m&range=1d"
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        res = requests.get(url, headers=headers, timeout=10).json()
        q = res['chart']['result'][0]['indicators']['quote'][0]
        data = []
        for i in range(len(q['close'])):
            if all([q['open'][i], q['high'][i], q['low'][i], q['close'][i]]):
                data.append({'o': q['open'][i], 'h': q['high'][i], 'l': q['low'][i], 'c': q['close'][i]})
        data = data[-60:]
        print(f"Yahoo {pair}: {data[-1]['c']:.5f} | {len(data)} شمعة")
        return data
    except Exception as e:
        print(f"Yahoo {pair}: {e}")
        return None

def calc_rsi(closes, period=14):
    if len(closes) < period + 1: return 50
    gains = [max(0, closes[i] - closes[i-1]) for i in range(1, len(closes))]
    losses = [max(0, closes[i-1] - closes[i]) for i in range(1, len(closes))]
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0: return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def candle_stats(c):
    body = abs(c['c'] - c['o'])
    total = c['h'] - c['l']
    if total == 0: return 0, 0, 0, 0
    upper = c['h'] - max(c['c'], c['o'])
    lower = min(c['c'], c['o']) - c['l']
    return body, upper, lower, total

def calc_support_resistance(data):
    if len(data) < 25: return 0, 0, 0, 0
    closes = [d['c'] for d in data]
    highs = [d['h'] for d in data]
    lows = [d['l'] for d in data]
    current = closes[-1]
    s1 = min(lows[-20:])
    r1 = max(highs[-20:])
    last_h = highs[-2]
    last_l = lows[-2]
    last_c = closes[-2]
    pp = (last_h + last_l + last_c) / 3
    s2 = 2 * pp - last_h
    r2 = 2 * pp - last_l
    price_levels = {}
    for c in closes[-30:]:
        level = round(c, 4)
        price_levels[level] = price_levels.get(level, 0) + 1
    sorted_levels = sorted(price_levels.items(), key=lambda x: x[1], reverse=True)
    s3 = sorted([p for p, _ in sorted_levels if p < current], reverse=True)[0] if any(p < current for p, _ in sorted_levels) else s1
    r3 = sorted([p for p, _ in sorted_levels if p > current])[0] if any(p > current for p, _ in sorted_levels) else r1
    supports = [s for s in [s1, s2, s3] if s < current and s > 0]
    resistances = [r for r in [r1, r2, r3] if r > current]
    final_support = max(supports) if supports else s1
    final_resistance = min(resistances) if resistances else r1
    return final_support, final_resistance, s1, r1

def find_peaks_valleys(data, lookback=20):
    highs = [d['h'] for d in data[-lookback:]]
    lows = [d['l'] for d in data[-lookback:]]
    peaks = []
    valleys = []
    for i in range(2, len(highs)-2):
        if highs[i] > highs[i-1] and highs[i] > highs[i-2] and highs[i] > highs[i+1] and highs[i] > highs[i+2]:
            peaks.append((i, highs[i]))
        if lows[i] < lows[i-1] and lows[i] < lows[i-2] and lows[i] < lows[i+1] and lows[i] < lows[i+2]:
            valleys.append((i, lows[i]))
    return peaks, valleys

def check_chart_patterns(data, support, resistance):
    if len(data) < PATTERN_LOOKBACK: return None, None, 0, "ضعيفة"
    closes = [d['c'] for d in data]
    highs = [d['h'] for d in data]
    lows = [d['l'] for d in data]
    current = closes[-1]
    peaks, valleys = find_peaks_valleys(data, PATTERN_LOOKBACK)

    if len(peaks) >= 3:
        left_shoulder = peaks[-3][1]
        head = peaks[-2][1]
        right_shoulder = peaks[-1][1]
        if head > left_shoulder and head > right_shoulder and abs(left_shoulder - right_shoulder) / left_shoulder < 0.015:
            neckline = min(valleys[-2][1], valleys[-1][1]) if len(valleys) >= 2 else support
            if current < neckline * 1.001:
                strength = ((head - neckline) / neckline) * 100
                return "بيع", f"رأس وكتفين | {strength:.2f}%", neckline, "قوية"

    if len(valleys) >= 3:
        left_shoulder = valleys[-3][1]
        head = valleys[-2][1]
        right_shoulder = valleys[-1][1]
        if head < left_shoulder and head < right_shoulder and abs(left_shoulder - right_shoulder) / left_shoulder < 0.015:
            neckline = max(peaks[-2][1], peaks[-1][1]) if len(peaks) >= 2 else resistance
            if current > neckline * 0.999:
                strength = ((neckline - head) / head) * 100
                return "شراء", f"رأس وكتفين مقلوب | {strength:.2f}%", neckline, "قوية"

    if len(peaks) >= 2:
        p1, p2 = peaks[-2][1], peaks[-1][1]
        if abs(p1 - p2) / p1 < 0.01:
            valley_between = min([lows[i] for i in range(peaks[-2][0], peaks[-1][0])])
            if current < valley_between * 1.001:
                strength = ((p1 - valley_between) / valley_between) * 100
                return "بيع", f"قمة مزدوجة | {strength:.2f}%", valley_between, "قوية"

    if len(valleys) >= 2:
        v1, v2 = valleys[-2][1], valleys[-1][1]
        if abs(v1 - v2) / v1 < 0.01:
            peak_between = max([highs[i] for i in range(valleys[-2][0], valleys[-1][0])])
            if current > peak_between * 0.999:
                strength = ((peak_between - v1) / v1) * 100
                return "شراء", f"قاع مزدوج | {strength:.2f}%", peak_between, "قوية"

    if len(peaks) >= 2 and len(valleys) >= 2:
        if abs(peaks[-1][1] - peaks[-2][1]) / peaks[-1][1] < 0.008:
            if valleys[-1][1] > valleys[-2][1]:
                if current > peaks[-1][1] * 0.999:
                    strength = ((peaks[-1][1] - valleys[-2][1]) / valleys[-2][1]) * 100
                    return "شراء", f"مثلث صاعد | {strength:.2f}%", peaks[-1][1], "قوية"

    if len(peaks) >= 2 and len(valleys) >= 2:
        if abs(valleys[-1][1] - valleys[-2][1]) / valleys[-1][1] < 0.008:
            if peaks[-1][1] < peaks[-2][1]:
                if current < valleys[-1][1] * 1.001:
                    strength = ((peaks[-2][1] - valleys[-1][1]) / valleys[-1][1]) * 100
                    return "بيع", f"مثلث هابط | {strength:.2f}%", valleys[-1][1], "قوية"

    if len(closes) >= 15:
        pole_start = min(lows[-15:-10])
        pole_end = max(highs[-10:-5])
        pole_height = (pole_end - pole_start) / pole_start
        if pole_height > 0.03:
            flag_highs = highs[-5:]
            flag_lows = lows[-5:]
            if max(flag_highs) < pole_end and min(flag_lows) > pole_start:
                if current > max(flag_highs) * 0.999:
                    strength = pole_height * 100
                    return "شراء", f"علم صاعد | {strength:.2f}%", max(flag_highs), "متوسطة"

    if len(closes) >= 15:
        pole_start = max(highs[-15:-10])
        pole_end = min(lows[-10:-5])
        pole_height = (pole_start - pole_end) / pole_start
        if pole_height > 0.03:
            flag_highs = highs[-5:]
            flag_lows = lows[-5:]
            if min(flag_lows) > pole_end and max(flag_highs) < pole_start:
                if current < min(flag_lows) * 1.001:
                    strength = pole_height * 100
                    return "بيع", f"علم هابط | {strength:.2f}%", min(flag_lows), "متوسطة"

    return None, None, 0, "ضعيفة"

def check_candlestick_patterns(data, support, resistance):
    if len(data) < 25: return None, None, 0, "ضعيفة"
    c0, c1, c2, c3 = data[-1], data[-2], data[-3], data[-4]
    b0, u0, l0, t0 = candle_stats(c0)
    b1, u1, l1, t1 = candle_stats(c1)
    b2, u2, l2, t2 = candle_stats(c2)
    b3, u3, l3, t3 = candle_stats(c3)

    if (c2['c'] < c2['o'] and c1['c'] > c1['o'] and c1['c'] > c2['o'] and c1['o'] < c2['c'] and
        c2['l'] <= support * 1.008 and c0['c'] > c1['h']):
        strength = ((c0['c'] - support) / support) * 100
        if strength >= MIN_PATTERN_STRENGTH:
            return "شراء", f"ابتلاع صاعد مؤكد | {strength:.2f}%", support, "قوية"

    if (l1 > b1 * 2 and u1 < b1 * 0.3 and c1['l'] <= support * 1.008 and c0['c'] > c1['h']):
        strength = ((c0['c'] - c1['l']) / c1['l']) * 100
        return "شراء", f"مطرقة مؤكدة | {strength:.2f}%", support, "قوية"

    if (c3['c'] < c3['o'] and b2 < t2 * 0.3 and c1['c'] > c1['o'] and
        c1['c'] > (c3['o'] + c3['c'])/2 and c0['c'] > c1['h']):
        strength = ((c0['c'] - c2['l']) / c2['l']) * 100
        return "شراء", f"نجمة الصباح | {strength:.2f}%", c2['l'], "قوية"

    if (c2['c'] > c2['o'] and c1['c'] > c1['o'] and c0['c'] > c0['o'] and
        c1['o'] > c2['o'] and c0['o'] > c1['o'] and c2['l'] <= support * 1.008):
        strength = ((c0['c'] - c2['o']) / c2['o']) * 100
        return "شراء", f"ثلاث جنود | {strength:.2f}%", support, "قوية"

    if (c2['c'] > c2['o'] and c1['c'] < c1['o'] and c1['c'] < c2['o'] and c1['o'] > c2['c'] and
        c2['h'] >= resistance * 0.992 and c0['c'] < c1['l']):
        strength = ((resistance - c0['c']) / resistance) * 100
        if strength >= MIN_PATTERN_STRENGTH:
            return "بيع", f"ابتلاع هابط مؤكد | {strength:.2f}%", resistance, "قوية"

    if (u1 > b1 * 2 and l1 < b1 * 0.3 and c1['h'] >= resistance * 0.992 and c0['c'] < c1['l']):
        strength = ((c1['h'] - c0['c']) / c1['h']) * 100
        return "بيع", f"شهاب مؤكد | {strength:.2f}%", resistance, "قوية"

    if (c3['c'] > c3['o'] and b2 < t2 * 0.3 and c1['c'] < c1['o'] and
        c1['c'] < (c3['o'] + c3['c'])/2 and c0['c'] < c1['l']):
        strength = ((c2['h'] - c0['c']) / c2['h']) * 100
        return "بيع", f"نجمة المساء | {strength:.2f}%", c2['h'], "قوية"

    if (c2['c'] < c2['o'] and c1['c'] < c1['o'] and c0['c'] < c0['o'] and
        c1['o'] < c2['o'] and c0['o'] < c1['o'] and c2['h'] >= resistance * 0.992):
        strength = ((c2['o'] - c0['c']) / c2['o']) * 100
        return "بيع", f"ثلاث غربان | {strength:.2f}%", resistance, "قوية"

    return None, None, 0, "ضعيفة"

def check(pair, news_list):
    data = get_candles(pair)
    if not data or len(data) < 30: return None

    closes = [d['c'] for d in data]
    current_price = closes[-1]
    ma3 = sum(closes[-3:]) / 3
    ma10 = sum(closes[-10:]) / 10
    prev_ma3 = sum(closes[-4:-1]) / 3
    prev_ma10 = sum(closes[-11:-1]) / 10
    rsi = calc_rsi(closes)
    support, resistance, _, _ = calc_support_resistance(data)

    print(f"{pair}: MA3={ma3:.5f} | MA10={ma10:.5f} | RSI={rsi:.1f} | Price={current_price:.5f}")

    direction, pattern_name, level, strength_level = check_chart_patterns(data, support, resistance)
    if not direction:
        direction, pattern_name, level, strength_level = check_candlestick_patterns(data, support, resistance)

    if not direction: return None

    pip_value = 0.0001 if "JPY" not in pair else 0.01
    if direction == "شراء":
        distance_pips = abs(current_price - support) / pip_value
        if distance_pips > MAX_DISTANCE_PIPS:
            print(f"{pair}: شراء مرفوض - بعيد عن الدعم {distance_pips:.1f} نقطة")
            return None
    else:
        distance_pips = abs(current_price - resistance) / pip_value
        if distance_pips > MAX_DISTANCE_PIPS:
            print(f"{pair}: بيع مرفوض - بعيد عن المقاومة {distance_pips:.1f} نقطة")
            return None

    buy_ok = (direction == "شراء" and prev_ma3 < prev_ma10 and ma3 > ma10 and rsi <= RSI_BUY_MAX)
    sell_ok = (direction == "بيع" and prev_ma3 > prev_ma10 and ma3 < ma10 and rsi >= RSI_SELL_MIN)

    if not buy_ok and not sell_ok:
        print(f"{pair}: {direction} مرفوض - الموفنج/RSI ما أكد")
        return None

    # نشوف اذا فيه خبر قوي يدعم الاشارة
    news_boost = False
    news_title = None
    for news in news_list:
        pair_currencies = pair.replace(" OTC", "").split("/")
        if any(curr in news['title'].lower() for curr in pair_currencies):
            news_boost = True
            news_title = news['title']
            strength_level = "قوية" # نقوي الاشارة اذا فيه خبر
            break

    arrow = "⬆️" if direction == "شراء" else "⬇️"
    level_name = "الدعم" if direction == "شراء" else "المقاومة"

    print(f"✅ {direction}: {pattern_name} | {strength_level} | دعم:{support:.5f} مقاومة:{resistance:.5f} | مسافة:{distance_pips:.1f}p | خبر:{news_boost}")
    return direction, arrow, rsi, pattern_name, level, level_name, strength_level, support, resistance, distance_pips, data, news_boost, news_title

if CHAT_ID:
    pairs_list = "\n".join([f"• {p}" for p in PAIRS])
    send(f"""🚀🚀 <b>البوت V7 اشتغل</b> 🚀🚀
<b>بوت ابو ركان - الاخبار + الشارت + 30 نمط</b>

<b>الأزواج 12:</b>
{pairs_list}

📊 <b>البيانات:</b> Yahoo 1d فقط
📰 <b>الاخبار:</b> Yahoo + TwelveData فلتر قوي جداً فقط
📐 <b>الأنماط:</b> 30+ نمط فني
<b>جديد:</b> رسم الشارت + دمج الاخبار القوية فقط
📍 <b>دعم ومقاومة:</b> تلقائي 3 طرق
🎯 <b>فلتر:</b> {MAX_DISTANCE_PIPS} نقاط من المستوى
⏱️ <b>المعاملة:</b> {TRADE_DURATION_MINUTES} دقيقة
⏳ <b>الدخول:</b> بعد {ENTRY_DELAY_SECONDS} ثانية""")
else:
    print("ارسل /start للبوت أول")

while True:
    if not CHAT_ID:
        CHAT_ID = get_chat_id()
        time.sleep(5)
        continue

    # نشيك الاخبار اول
    current_news = check_news_impact()

    for pair in PAIRS:
        if pair in last_signal and time.time() - last_signal < COOLDOWN_MINUTES * 60:
            continue

        result = check(pair, current_news)
        if result:
            direction, arrow, rsi, pattern, level, level_name, strength, sup, res, dist, chart_data, has_news, news_title = result
            entry_time = (datetime.now() + timedelta(seconds=ENTRY_DELAY_SECONDS)).strftime("%H:%M:%S")
            update_summary(direction, pair, strength, CHAT_ID, has_news)

            strength_emoji = "🔥" if strength == "قوية" else "🟡" if strength == "متوسطة" else "⚪"
            news_emoji = "📰" if has_news else ""

            msg = f"""❗️ <b>اضبط المؤقت 00:01:00</b> ❗️

📊 <b>Yahoo 1d</b> | {strength_emoji} <b>{strength}</b> {news_emoji}
زوج <b>{pair}</b>
<b>{direction} {arrow}</b> | RSI: {rsi:.1f}
📐 <b>{pattern}</b>
📍 {level_name}: <b>{level:.5f}</b> | مسافة: <b>{dist:.1f}p</b>
🟩 دعم: <b>{sup:.5f}</b> | 🟥 مقاومة: <b>{res:.5f}</b>"""
            if has_news and news_title:
                msg += f"\n📰 <b>خبر قوي:</b> {news_title[:60]}..."

            msg += f"""
⏱️ المعاملة: <b>{TRADE_DURATION_MINUTES} دقيقة</b>
🕐 الدخول: <b>{entry_time}</b>
<b>ادخل بعد {ENTRY_DELAY_SECONDS} ثانية</b>"""

            if SEND_CHART:
                chart_img = plot_chart(chart_data, pair, direction, pattern, sup, res, news_title)
                if chart_img:
                    send_photo(chart_img, msg)
                else:
                    send(msg)
            else:
                send(msg)

            last_signal = time.time()
        time.sleep(2)

    session_count += 1
    print(f"--- دورة {session_count} انتهت ---")
    if session_count % SUMMARY_EVERY_SESSIONS == 0:
        send_summary()
    time.sleep(15)
 candle_stats(c1)
    b2, u2, l2, t2 = candle_stats(c2)
    b3, u3, l3, t3 = candle_stats(c3)

    # ========== أنماط الشراء ==========

    # 1. ابتلاع صاعد + تأكيد
    if (c2['c'] < c2['o'] and c1['c'] > c1['o'] and c1['c'] > c2['o'] and c1['o'] < c2['c'] and
        c2['l'] <= support * 1.008 and c0['c'] > c1['h']):
        strength = ((c0['c'] - support) / support) * 100
        if strength >= MIN_PATTERN_STRENGTH:
            return "شراء", f"ابتلاع صاعد مؤكد | {strength:.2f}%", support, "قوية"

    # 2. مطرقة Hammer
    if (l1 > b1 * 2 and u1 < b1 * 0.3 and c1['l'] <= support * 1.008 and c0['c'] > c1['h']):
        strength = ((c0['c'] - c1['l']) / c1['l']) * 100
        return "شراء", f"مطرقة مؤكدة | {strength:.2f}%", support, "قوية"

    # 3. مطرقة مقلوبة Inverted Hammer
    if (u1 > b1 * 2 and l1 < b1 * 0.3 and c1['l'] <= support * 1.008 and c0['c'] > c1['h']):
        strength = ((c0['c'] - support) / support) * 100
        return "شراء", f"مطرقة مقلوبة | {strength:.2f}%", support, "متوسطة"

    # 4. نجمة الصباح Morning Star
    if (c3['c'] < c3['o'] and b2 < t2 * 0.3 and c1['c'] > c1['o'] and
        c1['c'] > (c3['o'] + c3['c'])/2 and c0['c'] > c1['h']):
        strength = ((c0['c'] - c2['l']) / c2['l']) * 100
        return "شراء", f"نجمة الصباح | {strength:.2f}%", c2['l'], "قوية"

    # 5. هرامي صاعد
    if (c2['c'] < c2['o'] and c1['c'] > c1['o'] and c1['o'] > c2['c'] and c1['c'] < c2['o'] and
        c0['c'] > c2['o']):
        strength = ((c0['c'] - c2['l']) / c2['l']) * 100
        return "شراء", f"هرامي صاعد | {strength:.2f}%", c2['l'], "متوسطة"

    # 6. ثلاث جنود بيض
    if (c2['c'] > c2['o'] and c1['c'] > c1['o'] and c0['c'] > c0['o'] and
        c1['o'] > c2['o'] and c0['o'] > c1['o'] and c2['l'] <= support * 1.008):
        strength = ((c0['c'] - c2['o']) / c2['o']) * 100
        return "شراء", f"ثلاث جنود | {strength:.2f}%", support, "قوية"

    # 7. دوجي دراجون فلاي + تأكيد
    if (b1 < t1 * 0.1 and l1 > t1 * 0.7 and u1 < t1 * 0.1 and
        c1['l'] <= support * 1.008 and c0['c'] > c1['o']):
        strength = ((c0['c'] - c1['l']) / c1['l']) * 100
        return "شراء", f"دوجي دراجون فلاي | {strength:.2f}%", support, "متوسطة"

    # 8. ملقط القاع Tweezers Bottom
    if (abs(c2['l'] - c1['l']) < c1['c'] * 0.0005 and c2['c'] < c2['o'] and c1['c'] > c1['o'] and
        c0['c'] > max(c2['o'], c1['c'])):
        strength = ((c0['c'] - c1['l']) / c1['l']) * 100
        return "شراء", f"ملقط قاع | {strength:.2f}%", c1['l'], "متوسطة"

    # 9. اختراق مقاومة + إعادة اختبار
    if (c2['c'] > resistance and c1['l'] <= resistance * 1.002 and c1['l'] >= resistance * 0.998 and
        c0['c'] > c1['h']):
        strength = ((c0['c'] - resistance) / resistance) * 100
        return "شراء", f"اختراق مقاومة | {strength:.2f}%", resistance, "قوية"

    # ========== أنماط البيع ==========

    # 1. ابتلاع هابط + تأكيد
    if (c2['c'] > c2['o'] and c1['c'] < c1['o'] and c1['c'] < c2['o'] and c1['o'] > c2['c'] and
        c2['h'] >= resistance * 0.992 and c0['c'] < c1['l']):
        strength = ((resistance - c0['c']) / resistance) * 100
        if strength >= MIN_PATTERN_STRENGTH:
            return "بيع", f"ابتلاع هابط مؤكد | {strength:.2f}%", resistance, "قوية"

    # 2. شهاب Shooting Star
    if (u1 > b1 * 2 and l1 < b1 * 0.3 and c1['h'] >= resistance * 0.992 and c0['c'] < c1['l']):
        strength = ((c1['h'] - c0['c']) / c1['h']) * 100
        return "بيع", f"شهاب مؤكد | {strength:.2f}%", resistance, "قوية"

    # 3. الرجل المشنوق Hanging Man
    if (l1 > b1 * 2 and u1 < b1 * 0.3 and c1['h'] >= resistance * 0.992 and c0['c'] < c1['l']):
        strength = ((resistance - c0['c']) / resistance) * 100
        return "بيع", f"رجل مشنوق | {strength:.2f}%", resistance, "متوسطة"

    # 4. نجمة المساء Evening Star
    if (c3['c'] > c3['o'] and b2 < t2 * 0.3 and c1['c'] < c1['o'] and
        c1['c'] < (c3['o'] + c3['c'])/2 and c0['c'] < c1['l']):
        strength = ((c2['h'] - c0['c']) / c2['h']) * 100
        return "بيع", f"نجمة المساء | {strength:.2f}%", c2['h'], "قوية"

    # 5. هرامي هابط
    if (c2['c'] > c2['o'] and c1['c'] < c1['o'] and c1['o'] < c2['c'] and c1['c'] > c2['o'] and
        c0['c'] < c2['o']):
        strength = ((c2['h'] - c0['c']) / c2['h']) * 100
        return "بيع", f"هرامي هابط | {strength:.2f}%", c2['h'], "متوسطة"

    # 6. ثلاث غربان سود
    if (c2['c'] < c2['o'] and c1['c'] < c1['o'] and c0['c'] < c0['o'] and
        c1['o'] < c2['o'] and c0['o'] < c1['o'] and c2['h'] >= resistance * 0.992):
        strength = ((c2['o'] - c0['c']) / c2['o']) * 100
        return "بيع", f"ثلاث غربان | {strength:.2f}%", resistance, "قوية"

    # 7. دوجي جريفستون + تأكيد
    if (b1 < t1 * 0.1 and u1 > t1 * 0.7 and l1 < t1 * 0.1 and
        c1['h'] >= resistance * 0.992 and c0['c'] < c1['o']):
        strength = ((c1['h'] - c0['c']) / c1['h']) * 100
        return "بيع", f"دوجي جريفستون | {strength:.2f}%", resistance, "متوسطة"

    # 8. ملقط القمة Tweezers Top
    if (abs(c2['h'] - c1['h']) < c1['c'] * 0.0005 and c2['c'] > c2['o'] and c1['c'] < c1['o'] and
        c0['c'] < min(c2['o'], c1['c'])):
        strength = ((c1['h'] - c0['c']) / c1['h']) * 100
        return "بيع", f"ملقط قمة | {strength:.2f}%", c1['h'], "متوسطة"

    # 9. كسر دعم + إعادة اختبار
    if (c2['c'] < support and c1['h'] >= support * 0.998 and c1['h'] <= support * 1.002 and
        c0['c'] < c1['l']):
        strength = ((support - c0['c']) / support) * 100
        return "بيع", f"كسر دعم | {strength:.2f}%", support, "قوية"

    return None, None, 0, "ضعيفة"

def check(pair):
    data = get_candles(pair)
    if not data or len(data) < 20: return None

    closes = [d['c'] for d in data]
    ma3 = sum(closes[-3:]) / 3
    ma10 = sum(closes[-10:]) / 10
    prev_ma3 = sum(closes[-4:-1]) / 3
    prev_ma10 = sum(closes[-11:-1]) / 10
    rsi = calc_rsi(closes)

    print(f"{pair}: MA3={ma3:.5f} | MA10={ma10:.5f} | RSI={rsi:.1f}")

    direction, pattern_name, level, strength_level = check_all_patterns(data)
    if not direction: return None

    buy_ok = (direction == "شراء" and prev_ma3 < prev_ma10 and ma3 > ma10 and rsi <= RSI_BUY_MAX)
    sell_ok = (direction == "بيع" and prev_ma3 > prev_ma10 and ma3 < ma10 and rsi >= RSI_SELL_MIN)

    if not buy_ok and not sell_ok:
        print(f"{pair}: {direction} مرفوض - الموفنج/RSI ما أكد")
        return None

    arrow = "⬆️" if direction == "شراء" else "⬇️"
    level_name = "الدعم" if direction == "شراء" else "المقاومة"

    print(f"✅ {direction}: {pattern_name} | {strength_level}")
    return direction, arrow, rsi, pattern_name, level, level_name, strength_level

if CHAT_ID:
    pairs_list = "\n".join([f"• {p}" for p in PAIRS])
    send(f"""🚀🚀 <b>البوت V3 اشتغل</b> 🚀🚀
<b>بوت ابو ركان - نسخة الأنماط الكاملة</b>

<b>الأزواج 12:</b>
{pairs_list}

📊 <b>البيانات:</b> Yahoo 1d فقط
📐 <b>الأنماط:</b> 18 نمط مع تأكيدات
- ابتلاع، مطرقة، شهاب، نجمة الصباح/المساء
- هرامي، دوجي، 3 جنود/غربان، ملقط
- اختراق/كسر دعم ومقاومة
⏱️ <b>المعاملة:</b> {TRADE_DURATION_MINUTES} دقيقة
⏳ <b>الدخول:</b> بعد {ENTRY_DELAY_SECONDS} ثانية
🎯 <b>أقل قوة:</b> {MIN_PATTERN_STRENGTH}%""")
else:
    print("ارسل /start للبوت أول")

while True:
    if not CHAT_ID:
        CHAT_ID = get_chat_id()
        time.sleep(5)
        continue

    for pair in PAIRS:
        if pair in last_signal and time.time() - last_signal[pair] < COOLDOWN_MINUTES * 60:
            continue

        result = check(pair)
        if result:
            direction, arrow, rsi, pattern, level, level_name, strength = result
            entry_time = (datetime.now() + timedelta(seconds=ENTRY_DELAY_SECONDS)).strftime("%H:%M:%S")
            update_summary(direction, pair, strength, CHAT_ID)

            strength_emoji = "🔥" if strength == "قوية" else "🟡" if strength == "متوسطة" else "⚪"
            msg = f"""❗️ <b>اضبط المؤقت 00:01:00</b> ❗️

📊 <b>Yahoo 1d</b> | {strength_emoji} <b>{strength}</b>
زوج <b>{pair}</b>
<b>{direction} {arrow}</b> | RSI: {rsi:.1f}
📐 <b>{pattern}</b>
📍 {level_name}: <b>{level:.5f}</b>
⏱️ المعاملة: <b>{TRADE_DURATION_MINUTES} دقيقة</b>
🕐 الدخول: <b>{entry_time}</b>
<b>ادخل بعد {ENTRY_DELAY_SECONDS} ثانية</b>"""
            send(msg)
            last_signal[pair] = time.time()
        time.sleep(2)

    session_count += 1
    print(f"--- دورة {session_count} انتهت ---")
    if session_count % SUMMARY_EVERY_SESSIONS == 0:
        send_summary()
    time.sleep(15)
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
