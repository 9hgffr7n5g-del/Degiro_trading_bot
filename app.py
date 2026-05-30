from flask import Flask, request, jsonify
import os, time, json, hmac, base64, hashlib, urllib.parse
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
    "last_buy_order_id": None,
    "last_sell_price": None,
    "last_sell_order_id": None,
    "last_action": None,
    "last_update_ts": None,
    "closed_trades": 0,
    "open_trade_id": None,
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
        return float(clean(value))
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


def trade_text(data, action, price):
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
        p = fval(points)
        result = "WIN" if p > 0 else "LOSS" if p < 0 else "FLAT"

    lines = []
    if buy:
        lines.append(f"BUY: {fmt(buy, 1)}")
    if sell:
        lines.append(f"SELL: {fmt(sell, 1)}")
    if points:
        lines.append(f"TRADE: {pts(points)} punten")
    if result:
        lines.append(f"RESULT: {result}")
    if reason:
        lines.append(f"REASON: {reason}")

    return "Trade resultaat\n" + "\n".join(lines) + "\n" if lines else ""


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
    return base_message(data) + trade_text(data, clean(data.get("action")), clean(data.get("price"))) + f"""

â ï¸ Kraken-order NIET uitgevoerd

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
last_action={s.get("last_action")}
last_buy_price={s.get("last_buy_price")}
last_sell_price={s.get("last_sell_price")}
open_trade_id={s.get("open_trade_id")}
"""


def update_buy_state(volume, price, oid):
    s = load_state()
    pos = fval(s.get("bot_position_btc"), 0.0)
    s["bot_position_btc"] = round(pos + volume, 8)
    s["last_buy_price"] = fval(price, None)
    s["last_buy_order_id"] = oid
    s["last_action"] = "BUY"
    s["open_trade_id"] = oid or f"buy-{int(time.time())}"
    save_state(s)


def update_sell_state(volume, price, oid):
    s = load_state()
    pos = fval(s.get("bot_position_btc"), 0.0)
    new_pos = max(0.0, pos - volume)
    s["bot_position_btc"] = round(new_pos, 8)
    s["last_sell_price"] = fval(price, None)
    s["last_sell_order_id"] = oid
    s["last_action"] = "SELL"
    if new_pos < MIN_BTC_VOLUME:
        s["open_trade_id"] = None
        s["closed_trades"] = int(s.get("closed_trades", 0) or 0) + 1
    save_state(s)


@app.route("/")
def home():
    return jsonify({
        "status": "Rene Kraken BTC Spot Bot draait",
        "pair": PAIR,
        "env_live_allowed": env_live_allowed(),
        "state": load_state()
    })


@app.route("/status")
def status():
    return jsonify({
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
            "KRAKEN_API_SECRET_SET": bool(KRAKEN_API_SECRET)
        },
        "state": load_state()
    })


@app.route("/reset_state", methods=["GET", "POST"])
def reset_state_route():
    s = reset_state()
    send_telegram("â ï¸ Bot-state handmatig gereset. Server denkt nu: geen botpositie open.")
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
            update_buy_state(volume_float, price, oid)
            msg = f"""â Kraken BUY uitgevoerd

Bot: {bot}
Ticker: {ticker}
Prijs: {price}
Amount: {volume} BTC
Order ID: {oid}
Reden: {clean(data.get("reason"))}
"""
        else:
            msg = base_message(data) + trade_text(data, action, price) + f"""

â ï¸ Kraken BUY NIET uitgevoerd

Volume:
{volume}

Kraken result:
{res}
"""
        send_telegram(msg)
        return "ok", 200

    if action == "BTC_EXIT":
        if bot_pos < MIN_BTC_VOLUME:
            send_telegram(blocked_message(data, "SELL geblokkeerd: server heeft geen bot-owned BTC positie geregistreerd. Dit voorkomt verkoop van privÃ©/eigen BTC."))
            return "ok", 200

        btc_balance = get_btc_balance()
        sell_volume = min(bot_pos, volume_float, btc_balance)

        if sell_volume < MIN_BTC_VOLUME:
            send_telegram(blocked_message(data, f"SELL genegeerd: onvoldoende verkoopbaar BTC. Botpositie: {bot_pos:.8f}, Kraken saldo: {btc_balance:.8f}, gevraagd: {volume}."))
            return "ok", 200

        res = kraken_sell(f"{sell_volume:.8f}")
        if order_ok(res):
            oid = order_id(res)
            update_sell_state(sell_volume, price, oid)
            msg = f"""â Kraken SELL uitgevoerd

Bot: {bot}
Ticker: {ticker}
Prijs: {price}
Amount: {sell_volume:.8f} BTC
Order ID: {oid}

""" + trade_text(data, action, price)
        else:
            msg = base_message(data) + trade_text(data, action, price) + f"""

â ï¸ Kraken SELL NIET uitgevoerd

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
