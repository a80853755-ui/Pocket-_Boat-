import os
import json
import logging
from datetime import date

log = logging.getLogger(__name__)
LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)

def _log_file() -> str:
    return os.path.join(LOG_DIR, f"trades-{date.today()}.log")

def log_trade(data: dict):
    try:
        line = json.dumps(data, ensure_ascii=False, default=str) + "\n"
        with open(_log_file(), "a", encoding="utf-8") as f:
            f.write(line)
    except Exception as ex:
        log.warning(f"خطأ في حفظ السجل: {ex}")

def log_event(event: str, **kwargs):
    log_trade({"event": event, **kwargs})

def read_today_logs() -> list[str]:
    try:
        with open(_log_file(), encoding="utf-8") as f:
            return [l.strip() for l in f if l.strip()]
    except FileNotFoundError:
        return []
