import json
import time
import logging
import threading
from typing import Callable, Optional
import websocket
import config
from analysis import Candle

log = logging.getLogger(__name__)

OnCandle = Callable[[str, list[Candle]], None]
OnResult = Callable[[str, str, float], None]   # (local_id, result, profit)

class PocketOptionClient:
    def __init__(self):
        self._ws:         Optional[websocket.WebSocketApp] = None
        self._thread:     Optional[threading.Thread] = None
        self._connected   = False
        self._candles:    dict[str, list[Candle]] = {}
        self._id_map:     dict[str, str] = {}   # server_id -> local_id
        self._on_candle:  Optional[OnCandle] = None
        self._on_result:  Optional[OnResult] = None
        self._running     = False
        self._ping_thread: Optional[threading.Thread] = None

    # ── Public API ──────────────────────────────────────────────────────────

    def set_on_candle(self, cb: OnCandle):  self._on_candle = cb
    def set_on_result(self, cb: OnResult):  self._on_result = cb
    def is_connected(self) -> bool:          return self._connected
    def get_candles(self, asset: str) -> list[Candle]:
        return self._candles.get(asset, [])

    def connect(self):
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def disconnect(self):
        self._running = False
        self._connected = False
        if self._ws:
            try: self._ws.close()
            except Exception: pass

    def open_trade(self, asset: str, direction: str, amount: float,
                   duration: int, local_id: str):
        payload = {
            "asset":      asset,
            "amount":     amount,
            "action":     direction,
            "isDemo":     1,
            "requestId":  local_id,
            "optionType": 100,
            "time":       duration,
        }
        self._send(f'42["openOrder",{json.dumps(payload)}]')
        log.info(f"صفقة أُرسلت: {asset} {direction} ${amount} {duration}ث")

    def subscribe_assets(self, assets: list[str]):
        for asset in assets:
            self._subscribe_asset(asset)

    # ── Internal ────────────────────────────────────────────────────────────

    def _run(self):
        ssid = config.POCKET_SSID
        url  = f"wss://api.po.market/socket.io/?EIO=4&transport=websocket&ssid={ssid}"
        while self._running:
            try:
                self._ws = websocket.WebSocketApp(
                    url,
                    header={
                        "Origin":     "https://pocketoption.com",
                        "User-Agent": "Mozilla/5.0",
                    },
                    on_open    = self._on_open,
                    on_message = self._on_message,
                    on_error   = self._on_error,
                    on_close   = self._on_close,
                )
                self._ws.run_forever(ping_interval=25, ping_timeout=10)
            except Exception as ex:
                log.error(f"خطأ WebSocket: {ex}")
            if self._running:
                log.info("إعادة اتصال بعد 5 ثوانٍ...")
                time.sleep(5)

    def _on_open(self, ws):
        self._connected = True
        log.info("✅ متصل بـ Pocket Option")
        auth = json.dumps({
            "session": config.POCKET_SSID,
            "isDemo":  1,
            "uid":     0,
            "platform": 2,
        })
        self._send(f'42["auth",{auth}]')
        from state import state
        self.subscribe_assets(state.currencies)

    def _on_message(self, ws, raw: str):
        if raw == "2":
            ws.send("3")
            return
        if not raw.startswith("42"):
            return
        try:
            data = json.loads(raw[2:])
            if not isinstance(data, list) or len(data) < 2:
                return
            event, payload = data[0], data[1]
            if event in ("candles", "history"):
                self._handle_candles(payload)
            elif event == "successopenOrder":
                log.info(f"صفقة مفتوحة: {payload.get('id')}")
            elif event in ("orderClosed", "successcloseOrder"):
                self._handle_close(payload)
        except Exception as ex:
            log.debug(f"parse error: {ex}")

    def _on_error(self, ws, error):
        log.error(f"خطأ WS: {error}")
        self._connected = False

    def _on_close(self, ws, code, msg):
        self._connected = False
        log.info(f"WS مغلق ({code})")

    def _handle_candles(self, payload: dict):
        asset = payload.get("asset") or payload.get("symbol")
        if not asset:
            return
        raw_list = payload.get("candles") or payload.get("data") or []
        candles: list[Candle] = []
        for c in raw_list:
            try:
                candles.append(Candle(
                    time  = int(c.get("time") or c.get("t", 0)),
                    open  = float(c.get("open") or c.get("o", 0)),
                    high  = float(c.get("high") or c.get("h", 0)),
                    low   = float(c.get("low")  or c.get("l", 0)),
                    close = float(c.get("close") or c.get("c", 0)),
                ))
            except Exception:
                continue
        if not candles:
            return
        existing = self._candles.get(asset, [])
        merged = {c.time: c for c in existing + candles}
        sorted_candles = sorted(merged.values(), key=lambda c: c.time)[-100:]
        self._candles[asset] = sorted_candles
        if self._on_candle:
            self._on_candle(asset, sorted_candles)

    def _handle_close(self, payload: dict):
        sid    = str(payload.get("id", ""))
        profit = float(payload.get("profit") or payload.get("win") or 0)
        result = "win" if profit > 0 else ("loss" if profit < 0 else "draw")
        local  = self._id_map.pop(sid, sid)
        log.info(f"صفقة مغلقة: {sid} → {result} ${profit:.2f}")
        if self._on_result:
            self._on_result(local, result, profit)

    def _subscribe_asset(self, asset: str):
        self._send(f'42["subscribeSymbol",{{"asset":"{asset}","period":60}}]')
        ts = int(time.time())
        self._send(
            f'42["loadHistoryPeriod",'
            f'{{"asset":"{asset}","index":0,"time":{ts},"offset":50,"period":60}}]'
        )

    def _send(self, msg: str):
        if self._ws and self._connected:
            try:
                self._ws.send(msg)
            except Exception as ex:
                log.debug(f"send error: {ex}")


po_client = PocketOptionClient()
