from flask import Flask, request, jsonify
import os
import time
import json
import hmac
import base64
import hashlib
import urllib.parse
import requests
from threading import Lock

app = Flask(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
KRAKEN_API_KEY = os.environ.get("KRAKEN_API_KEY")
KRAKEN_API_SECRET = os.environ.get("KRAKEN_API_SECRET")

TRADE_MODE_ENV = os.environ.get("TRADE_MODE", "")
BOT_MODE_ENV = os.environ.get("BOT_MODE", "")
EXECUTE_ORDERS_ENV = os.environ.get("EXECUTE_ORDERS", "")
LIVE_TRADING_ENV = os.environ.get("LIVE_TRADING", "")
DRY_RUN_ENV = os.environ.get("DRY_RUN", "")
PAPER_TRADING_ENV = os.environ.get("PAPER_TRADING", "")
TELEGRAM_ONLY_ENV = os.environ.get("TELEGRAM_ONLY", "")
EXCHANGE_ENV = os.environ.get("EXCHANGE", "")
MARKET_ENV = os.environ.get("MARKET", "")

KRAKEN_URL = "https://api.kraken.com"
PAIR = os.environ.get("KRAKEN_PAIR", "XBTEUR")
DEFAULT_BTC_VOLUME = os.environ.get("DEFAULT_BTC_VOLUME", "0.00010")
MIN_BTC_VOLUME = float(os.environ.get("MIN_BTC_VOLUME", "0.00010"))

STATE_FILE = os.environ.get("BOT_STATE_FILE", "bot_state.json")
STATE_LOCK = Lock()

DEFAULT_STATE = {
    "bot_position_btc": 0.0,
    "last_buy_price": None,
    "avg_entry_price": None,
    "last_buy_volume": 0.0,
    "last_buy_order_id": None,
    "last_buy_ts": None,
    "last_sell_price": None,
    "last_sell_volume": 0.0,
    "last_sell_order_id": None,
    "last_sell_ts": None,
    "last_action": None,
    "last_update_ts": None,
    "closed_trades": 0,
    "open_trade_id": None,
    "last_trade_points": None,
    "last_trade_gross_eur": None,
    "last_trade_result": None,
    "last_trade_entry_price": None,
    "last_trade_exit_price": None,
    "last_trade_volume": None,
    "last_pine_entry_price": None,
    "last_pine_entry_diff": None,
    "last_pine_result_warning": "",
    "server_started_ts": int(time.time())
}


def clean(value):
    if value is None:
        return ""
    s = str(value).strip()
    if s.lower() in ["", "none", "null", "nan"]:
        return ""
    return s


def bval(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ["true", "1", "yes", "ja", "on"]


def fval(value, default=0.0):
    try:
        s = clean(value)
        if s == "":
            return default
        return float(s)
    except Exception:
        return default


def fmt(value, decimals=1):
    try:
        return f"{float(value):.{decimals}f}"
    except Exception:
        return str(value)


def pts(value):
    try:
        x = float(value)
        sign = "+" if x > 0 else ""
        return f"{sign}{x:.1f}"
    except Exception:
        return str(value)


def result_from_points(points_value):
    p = fval(points_value, 0.0)
    if p > 0:
        return "WIN"
    if p < 0:
        return "LOSS"
    return "FLAT"


def send_telegram(message):
    if not BOT_TOKEN or not CHAT_ID:
        print("Telegram not configured")
        print(message)
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": CHAT_ID, "text": message}, timeout=15)
    except Exception as e:
        print("Telegram error:", e)


def load_state():
    with STATE_LOCK:
        try:
            if os.path.exists(STATE_FILE):
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                s = DEFAULT_STATE.copy()
                s.update(data)
                return s
        except Exception as e:
            print("State load error:", e)
        return DEFAULT_STATE.copy()


def save_state(state):
    with STATE_LOCK:
        state["last_update_ts"] = int(time.time())
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)


def reset_state():
    s = DEFAULT_STATE.copy()
    s["server_started_ts"] = int(time.time())
    save_state(s)
    return s


def env_live_allowed():
    live_on = (
        TRADE_MODE_ENV == "KRAKEN_LIVE"
        or BOT_MODE_ENV == "KRAKEN_LIVE"
        or bval(EXECUTE_ORDERS_ENV)
        or bval(LIVE_TRADING_ENV)
    )
    live_off = bval(DRY_RUN_ENV) or bval(PAPER_TRADING_ENV) or bval(TELEGRAM_ONLY_ENV)
    return live_on and not live_off


def json_live_requested(data):
    mode = clean(data.get("trade_mode"))
    live_on = (
        bval(data.get("live"))
        or bval(data.get("is_live"))
        or bval(data.get("kraken_order"))
        or bval(data.get("place_order"))
        or bval(data.get("execute"))
        or bval(data.get("live_order"))
        or mode == "KRAKEN_LIVE"
        or clean(data.get("mode")) == "KRAKEN_LIVE"
    )
    live_off = bval(data.get("telegram_only")) or bval(data.get("dry_run")) or bval(data.get("paper"))
    return live_on and not live_off


def supported_bot(data):
    bot = clean(data.get("bot")).upper()
    ticker = clean(data.get("ticker")).upper()
    action = clean(data.get("action"))
    version = clean(data.get("v_version")).upper()
    strategy = clean(data.get("strategy_base")).upper()

    ticker_ok = ticker in ["BTCEUR", "BTCEUR.P", "XBT/EUR", "XBTEUR"]
    action_ok = action in ["BTC_BUY", "BTC_EXIT"]
    bot_ok = (
        "RENE BTC SPOT BOT KRAKEN" in bot
        or ("RBT" in bot and "KRAKEN" in bot)
        or "KRAKEN" in version
        or "KRAKEN" in strategy
    )
    return ticker_ok and action_ok and bot_ok


def kraken_signature(urlpath, data, secret):
    postdata = urllib.parse.urlencode(data)
    encoded = (str(data["nonce"]) + postdata).encode()
    message = urlpath.encode() + hashlib.sha256(encoded).digest()
    mac = hmac.new(base64.b64decode(secret), message, hashlib.sha512)
    return base64.b64encode(mac.digest()).decode()


def kraken_private(endpoint, data):
    if not KRAKEN_API_KEY or not KRAKEN_API_SECRET:
        return {"error": ["LOCAL: Missing KRAKEN_API_KEY or KRAKEN_API_SECRET"], "result": {}}

    nonce = str(time.time_ns())
    urlpath = f"/0/private/{endpoint}"
    data["nonce"] = nonce
    headers = {
        "API-Key": KRAKEN_API_KEY,
        "API-Sign": kraken_signature(urlpath, data, KRAKEN_API_SECRET)
    }
    try:
        r = requests.post(KRAKEN_URL + urlpath, headers=headers, data=data, timeout=20)
        return r.json()
    except Exception as e:
        return {"error": [f"LOCAL: Kraken request failed: {e}"], "result": {}}


def get_btc_balance():
    res = kraken_private("Balance", {})
    try:
        return float(res.get("result", {}).get("XXBT", 0))
    except Exception:
        return 0.0


def kraken_buy(volume):
    return kraken_private("AddOrder", {
        "ordertype": "market",
        "type": "buy",
        "volume": str(volume),
        "pair": PAIR
    })


def kraken_sell(volume):
    return kraken_private("AddOrder", {
        "ordertype": "market",
        "type": "sell",
        "volume": str(volume),
        "pair": PAIR
    })


def order_ok(result):
    return not result.get("error") and bool(result.get("result", {}).get("txid"))


def order_id(result):
    txid = result.get("result", {}).get("txid", "")
    if isinstance(txid, list):
        return ", ".join(txid)
    return str(txid)


def get_volume(data, action):
    if action == "BTC_EXIT":
        keys = ["sell_amount_btc", "max_sell_btc", "amount_btc", "volume", "qty", "quantity"]
    else:
        keys = ["buy_amount_btc", "amount_btc", "volume", "qty", "quantity"]

    for k in keys:
        v = clean(data.get(k))
        if v:
            try:
                x = float(v)
                if x > 0:
                    return f"{x:.8f}"
            except Exception:
                pass

    return DEFAULT_BTC_VOLUME


def pine_trade_text(data, action, price):
    """Alleen debug/info. Niet leidend voor echte server trade-resultaten."""
    reason = clean(data.get("exit_reason")) or clean(data.get("reason"))
    if action == "BTC_BUY":
        return f"Reden: {reason}\n" if reason else ""

    buy = clean(data.get("trade_buy")) or clean(data.get("trade_entry_price")) or clean(data.get("entry_price"))
    sell = clean(data.get("trade_sell")) or clean(data.get("trade_exit_price")) or clean(data.get("exit_price")) or clean(price)
    points = clean(data.get("trade_points")) or clean(data.get("trade_gross_points")) or clean(data.get("trade_net_points"))
    result = clean(data.get("trade_result_simple")) or clean(data.get("trade_result"))

    if not points and buy and sell:
        points = str(fval(sell) - fval(buy))
    if not result and points:
        result = result_from_points(points)

    lines = []
    if buy:
        lines.append(f"PINE BUY: {fmt(buy, 1)}")
    if sell:
        lines.append(f"PINE SELL: {fmt(sell, 1)}")
    if points:
        lines.append(f"PINE TRADE: {pts(points)} punten")
    if result:
        lines.append(f"PINE RESULT: {result}")
    if reason:
        lines.append(f"PINE REASON: {reason}")

    return "Pine info/debug\n" + "\n".join(lines) + "\n" if lines else ""


def get_pine_entry(data):
    return (
        clean(data.get("trade_buy"))
        or clean(data.get("trade_entry_price"))
        or clean(data.get("entry_price"))
    )


def server_trade_result_text(entry_price, exit_price, volume, reason="", pine_entry="", order_id_value=""):
    entry = fval(entry_price, None)
    exitp = fval(exit_price, None)
    vol = fval(volume, 0.0)

    lines = ["Server trade resultaat"]
    if entry is not None:
        lines.append(f"SERVER BUY: {fmt(entry, 1)}")
    if exitp is not None:
        lines.append(f"SERVER SELL: {fmt(exitp, 1)}")
    if vol > 0:
        lines.append(f"SERVER VOLUME: {vol:.8f} BTC")
    if entry is not None and exitp is not None:
        trade_points = exitp - entry
        gross_eur = trade_points * vol
        lines.append(f"SERVER TRADE: {pts(trade_points)} punten")
        lines.append(f"SERVER GROSS EUR: {gross_eur:+.4f}")
        lines.append(f"SERVER RESULT: {result_from_points(trade_points)}")
    if order_id_value:
        lines.append(f"SELL ORDER ID: {order_id_value}")
    if reason:
        lines.append(f"REASON: {reason}")

    if pine_entry:
        pine = fval(pine_entry, None)
        if pine is not None and entry is not None:
            diff = pine - entry
            lines.append("")
            lines.append("Controle Pine vs server")
            lines.append(f"PINE ENTRY: {fmt(pine, 1)}")
            lines.append(f"SERVER ENTRY: {fmt(entry, 1)}")
            lines.append(f"VERSCHIL: {pts(diff)} punten")
            if abs(diff) >= 1.0:
                lines.append("LET OP - Pine-entry wijkt af. Server/Kraken-resultaat is leidend.")

    return "\n".join(lines) + "\n"


def base_message(data):
    return f"""Trading Alert

Bot: {clean(data.get("bot"))}
Ticker: {clean(data.get("ticker"))}
Actie: {clean(data.get("action"))}
Prijs: {clean(data.get("price"))}
Timeframe: {clean(data.get("timeframe")) or clean(data.get("tf"))}
"""


def blocked_message(data, reason):
    s = load_state()
    return base_message(data) + pine_trade_text(data, clean(data.get("action")), clean(data.get("price"))) + f"""

LET OP - Kraken-order NIET uitgevoerd

Reden:
{reason}

Diagnose:
trade_mode_json: {clean(data.get("trade_mode"))}
mode_json: {clean(data.get("mode"))}
live_json: {clean(data.get("live"))}
execute_json: {clean(data.get("execute"))}
place_order_json: {clean(data.get("place_order"))}
kraken_order_json: {clean(data.get("kraken_order"))}
telegram_only_json: {clean(data.get("telegram_only"))}
dry_run_json: {clean(data.get("dry_run"))}
paper_json: {clean(data.get("paper"))}
allow_add_buy_json: {clean(data.get("allow_add_buy"))}

Render env:
TRADE_MODE={TRADE_MODE_ENV}
BOT_MODE={BOT_MODE_ENV}
EXECUTE_ORDERS={EXECUTE_ORDERS_ENV}
LIVE_TRADING={LIVE_TRADING_ENV}
DRY_RUN={DRY_RUN_ENV}
PAPER_TRADING={PAPER_TRADING_ENV}
TELEGRAM_ONLY={TELEGRAM_ONLY_ENV}
EXCHANGE={EXCHANGE_ENV}
MARKET={MARKET_ENV}
KRAKEN_API_KEY_SET={bool(KRAKEN_API_KEY)}
KRAKEN_API_SECRET_SET={bool(KRAKEN_API_SECRET)}

Server bot-state:
bot_position_btc={s.get("bot_position_btc")}
avg_entry_price={s.get("avg_entry_price")}
last_buy_price={s.get("last_buy_price")}
last_buy_volume={s.get("last_buy_volume")}
last_buy_order_id={s.get("last_buy_order_id")}
last_sell_price={s.get("last_sell_price")}
last_trade_points={s.get("last_trade_points")}
last_trade_result={s.get("last_trade_result")}
open_trade_id={s.get("open_trade_id")}
"""


def update_buy_state(volume, price, oid):
    s = load_state()
    old_pos = fval(s.get("bot_position_btc"), 0.0)
    buy_price = fval(price, None)
    old_avg = fval(s.get("avg_entry_price"), None)

    new_pos = round(old_pos + volume, 8)
    if buy_price is not None:
        if old_pos >= MIN_BTC_VOLUME and old_avg is not None:
            new_avg = ((old_pos * old_avg) + (volume * buy_price)) / new_pos
        else:
            new_avg = buy_price
    else:
        new_avg = old_avg

    s["bot_position_btc"] = new_pos
    s["last_buy_price"] = buy_price
    s["avg_entry_price"] = new_avg
    s["last_buy_volume"] = volume
    s["last_buy_order_id"] = oid
    s["last_buy_ts"] = int(time.time())
    s["last_action"] = "BUY"
    s["open_trade_id"] = s.get("open_trade_id") or oid or f"buy-{int(time.time())}"
    save_state(s)
    return s


def update_sell_state(volume, price, oid, data):
    s = load_state()
    old_pos = fval(s.get("bot_position_btc"), 0.0)
    entry_price = fval(s.get("avg_entry_price"), None)
    last_buy_price = fval(s.get("last_buy_price"), None)
    if entry_price is None:
        entry_price = last_buy_price

    sell_price = fval(price, None)
    pine_entry = get_pine_entry(data)
    pine_entry_float = fval(pine_entry, None)

    trade_points = None
    gross_eur = None
    trade_result = None
    warning = ""

    if entry_price is not None and sell_price is not None:
        trade_points = sell_price - entry_price
        gross_eur = trade_points * volume
        trade_result = result_from_points(trade_points)

    if pine_entry_float is not None and entry_price is not None:
        diff = pine_entry_float - entry_price
        s["last_pine_entry_diff"] = diff
        if abs(diff) >= 1.0:
            warning = "Pine-entry wijkt af van server-entry. Server-resultaat is leidend."

    new_pos = max(0.0, old_pos - volume)
    s["bot_position_btc"] = round(new_pos, 8)
    s["last_sell_price"] = sell_price
    s["last_sell_volume"] = volume
    s["last_sell_order_id"] = oid
    s["last_sell_ts"] = int(time.time())
    s["last_action"] = "SELL"
    s["last_trade_points"] = trade_points
    s["last_trade_gross_eur"] = gross_eur
    s["last_trade_result"] = trade_result
    s["last_trade_entry_price"] = entry_price
    s["last_trade_exit_price"] = sell_price
    s["last_trade_volume"] = volume
    s["last_pine_entry_price"] = pine_entry_float
    s["last_pine_result_warning"] = warning

    if new_pos < MIN_BTC_VOLUME:
        s["open_trade_id"] = None
        s["closed_trades"] = int(s.get("closed_trades", 0) or 0) + 1
        s["avg_entry_price"] = None
    else:
        # Partial sell: keep avg_entry_price for remaining bot position.
        s["avg_entry_price"] = entry_price

    save_state(s)
    return s


@app.route("/")
def home():
    return jsonify({
        "status": "Rene Kraken BTC Spot Bot draait",
        "version": "app.py V9.14H SERVER RESULT FIX",
        "pair": PAIR,
        "env_live_allowed": env_live_allowed(),
        "state": load_state()
    })


@app.route("/status")
def status():
    return jsonify({
        "version": "app.py V9.14H SERVER RESULT FIX",
        "env_live_allowed": env_live_allowed(),
        "env": {
            "TRADE_MODE": TRADE_MODE_ENV,
            "BOT_MODE": BOT_MODE_ENV,
            "EXECUTE_ORDERS": EXECUTE_ORDERS_ENV,
            "LIVE_TRADING": LIVE_TRADING_ENV,
            "DRY_RUN": DRY_RUN_ENV,
            "PAPER_TRADING": PAPER_TRADING_ENV,
            "TELEGRAM_ONLY": TELEGRAM_ONLY_ENV,
            "EXCHANGE": EXCHANGE_ENV,
            "MARKET": MARKET_ENV,
            "KRAKEN_API_KEY_SET": bool(KRAKEN_API_KEY),
            "KRAKEN_API_SECRET_SET": bool(KRAKEN_API_SECRET),
            "BOT_STATE_FILE": STATE_FILE
        },
        "state": load_state()
    })


@app.route("/reset_state", methods=["GET", "POST"])
def reset_state_route():
    s = reset_state()
    send_telegram("LET OP - Bot-state handmatig gereset. Server denkt nu: geen botpositie open.")
    return jsonify({"status": "reset", "state": s})


@app.route("/send")
def send_test():
    send_telegram("TEST BERICHT VAN RENDER BOT")
    return "test gestuurd"


@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json or {}
    action = clean(data.get("action"))
    price = clean(data.get("price"))
    bot = clean(data.get("bot"))
    ticker = clean(data.get("ticker"))
    reason = clean(data.get("exit_reason")) or clean(data.get("reason"))

    if not supported_bot(data):
        send_telegram(blocked_message(data, "Bot/ticker/action wordt niet herkend als ondersteunde Kraken BTC bot."))
        return "ok", 200

    if not json_live_requested(data):
        send_telegram(blocked_message(data, "TradingView JSON vraagt geen live Kraken-order aan."))
        return "ok", 200

    if not env_live_allowed():
        send_telegram(blocked_message(data, "Render environment staat live trading niet toe of dry-run/telegram-only staat nog aan."))
        return "ok", 200

    volume = get_volume(data, action)
    volume_float = fval(volume)

    if volume_float < MIN_BTC_VOLUME:
        send_telegram(blocked_message(data, f"Ordervolume te laag: {volume}. Minimum is {MIN_BTC_VOLUME:.8f} BTC."))
        return "ok", 200

    state = load_state()
    bot_pos = fval(state.get("bot_position_btc"), 0.0)

    if action == "BTC_BUY":
        allow_add = bval(data.get("allow_add_buy"))
        if bot_pos >= MIN_BTC_VOLUME and not allow_add:
            send_telegram(blocked_message(data, f"BUY geblokkeerd: server heeft al botpositie {bot_pos:.8f} BTC en allow_add_buy=false."))
            return "ok", 200

        res = kraken_buy(volume)
        if order_ok(res):
            oid = order_id(res)
            new_state = update_buy_state(volume_float, price, oid)
            msg = f"""OK - Kraken BUY uitgevoerd

Bot: {bot}
Ticker: {ticker}
Prijs: {price}
Amount: {volume} BTC
Order ID: {oid}
Reden: {clean(data.get("reason"))}

Server positie:
bot_position_btc: {new_state.get("bot_position_btc")}
avg_entry_price: {fmt(new_state.get("avg_entry_price"), 1)}
last_buy_price: {fmt(new_state.get("last_buy_price"), 1)}
"""
        else:
            msg = base_message(data) + pine_trade_text(data, action, price) + f"""

LET OP - Kraken BUY NIET uitgevoerd

Volume:
{volume}

Kraken result:
{res}
"""
        send_telegram(msg)
        return "ok", 200

    if action == "BTC_EXIT":
        if bot_pos < MIN_BTC_VOLUME:
            send_telegram(blocked_message(data, "SELL geblokkeerd: server heeft geen bot-owned BTC positie geregistreerd. Dit voorkomt verkoop van prive/eigen BTC."))
            return "ok", 200

        btc_balance = get_btc_balance()
        sell_volume = min(bot_pos, volume_float, btc_balance)

        if sell_volume < MIN_BTC_VOLUME:
            send_telegram(blocked_message(data, f"SELL genegeerd: onvoldoende verkoopbaar BTC. Botpositie: {bot_pos:.8f}, Kraken saldo: {btc_balance:.8f}, gevraagd: {volume}."))
            return "ok", 200

        # Capture server entry before state changes.
        entry_before = fval(state.get("avg_entry_price"), None)
        if entry_before is None:
            entry_before = fval(state.get("last_buy_price"), None)

        res = kraken_sell(f"{sell_volume:.8f}")
        if order_ok(res):
            oid = order_id(res)
            new_state = update_sell_state(sell_volume, price, oid, data)
            msg = f"""OK - Kraken SELL uitgevoerd

Bot: {bot}
Ticker: {ticker}
Prijs: {price}
Amount: {sell_volume:.8f} BTC
Order ID: {oid}

""" + server_trade_result_text(
                entry_price=entry_before,
                exit_price=price,
                volume=sell_volume,
                reason=reason,
                pine_entry=get_pine_entry(data),
                order_id_value=oid
            ) + f"""
Server positie na SELL:
bot_position_btc: {new_state.get("bot_position_btc")}
closed_trades: {new_state.get("closed_trades")}
"""
        else:
            msg = base_message(data) + pine_trade_text(data, action, price) + f"""

LET OP - Kraken SELL NIET uitgevoerd

Volume:
{sell_volume:.8f}

Kraken result:
{res}
"""
        send_telegram(msg)
        return "ok", 200

    send_telegram(blocked_message(data, f"Onbekende actie: {action}"))
    return "ok", 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
