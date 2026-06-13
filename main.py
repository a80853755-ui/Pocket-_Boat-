"""
بوت إشارات Pocket Option — يرسل إشارات تيليجرام فقط
المستخدم يفتح الصفقات يدوياً على Pocket Option
"""
import config
TOKEN = config.TOKEN"8693601571:AAG8wR7YYl171g4VQboV4b5bJmO8Qer8eGc"
CHAT_ID = config.CHAT_ID"5690085743"
import asyncio
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import json

import config
from engine import run_cycle, set_telegram_sender
from telegram_bot import build_app, send_message
from trade_logger import log_event

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("main")


# ── Health Server ─────────────────────────────────────────────────────────────

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        from state import state
        if self.path in ("/api/healthz", "/healthz", "/"):
            body = json.dumps({
                "status":        "ok",
                "running":       state.is_running,
                "signals_today": state.stats.total,
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass


def start_health_server():
    server = HTTPServer(("0.0.0.0", config.PORT), HealthHandler)
    log.info(f"Health server على port {config.PORT}")
    threading.Thread(target=server.serve_forever, daemon=True).start()


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    if not config.TELEGRAM_TOKEN:
        log.error("❌ TELEGRAM_BOT_TOKEN غير موجود في Secrets")
        return

    start_health_server()
    set_telegram_sender(send_message)

    tg_app = build_app()

    # الطريقة الصحيحة لـ python-telegram-bot 20.x مع asyncio
    async with tg_app:
        # حذف webhook + إغلاق أي polling قديم
        await tg_app.bot.delete_webhook(drop_pending_updates=True)
        log.info("🧹 تم حذف webhook القديم")

        # إجبار إغلاق أي long-poll قديم على سيرفر Telegram
        try:
            await tg_app.bot.get_updates(offset=-1, timeout=0)
            log.info("🔓 تم تحرير الـ polling القديم")
        except Exception:
            pass

        await asyncio.sleep(2)

        # تشغيل البوت
        await tg_app.start()
        await tg_app.updater.start_polling(
            drop_pending_updates=True,
            allowed_updates=["message", "callback_query"],
        )

        log_event("bot_started")
        log.info("✅ البوت يستمع — أرسل /start من تيليجرام")

        # شغّل محرك الإشارات (يعمل بشكل متوازٍ مع الـ polling)
        engine_task = asyncio.create_task(run_cycle())

        try:
            await engine_task
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            await tg_app.updater.stop()
            await tg_app.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("البوت متوقف.")
