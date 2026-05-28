"""
بوت تداول Pocket Option — Python
التحكم الكامل من تيليجرام
"""

import asyncio
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import json

import config
from pocket_option import po_client
from engine import run_cycle, on_trade_result, set_telegram_sender
from telegram_bot import build_app, send_message
from trade_logger import log_event

# ── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("main")


# ── Health Server ─────────────────────────────────────────────────────────
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        from state import state

        if self.path in ("/api/healthz", "/healthz", "/"):
            body = json.dumps(
                {
                    "status": "ok",
                    "running": state.is_running,
                    "paused": state.is_paused,
                    "connected": po_client.is_connected(),
                    "daily_profit": state.stats.profit,
                    "win_rate": state.stats.win_rate,
                    "trades_today": state.stats.total,
                }
            ).encode()
            self.send_response(200)
