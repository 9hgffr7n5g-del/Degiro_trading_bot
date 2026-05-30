from flask import Flask, request
import requests
import os
import time
import hashlib
import hmac
import base64
import urllib.parse
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
PAIR = "XBTEUR"
DEFAULT_BTC_VOLUME = "0.00010"
MIN_BTC_VOLUME = 0.00010
def send_telegram(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": CHAT_ID, "text": message}, timeout=15)
    except Exception as e:
        print(f"Telegram error: {e}")
def clean_value(value):
    if value is None:
        return ""
    value_str = str(value).strip()
    if value_str.lower() in ["", "none", "null", "nan"]:
        return ""
    return value_str
def bool_value(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ["true", "1", "yes", "ja", "on"]
def fmt_number(value, decimals=1):
    value = clean_value(value)
    if value == "":
        return ""
    try:
        return f"{float(value):.{decimals}f}"
    except Exception:
        return str(value)
def fmt_points(value):
    value = clean_value(value)
    if value == "":
        return ""
    try:
        number = float(value)
        sign = "+" if number > 0 else ""
        return f"{sign}{number:.1f}"
    except Exception:
        return str(value)
def kraken_signature(urlpath, data, secret):
    postdata = urllib.parse.urlencode(data)
    encoded = (str(data["nonce"]) + postdata).encode()
    message = urlpath.encode() + hashlib.sha256(encoded).digest()
    mac = hmac.new(base64.b64decode(secret), message, hashlib.sha512)
    return base64.b64encode(mac.digest()).decode()
def kraken_private_request(endpoint, data):
    if not KRAKEN_API_KEY or not KRAKEN_API_SECRET:
        return {
            "error": ["LOCAL: Missing KRAKEN_API_KEY or KRAKEN_API_SECRET"],
            "result": {}
        }
    nonce = str(time.time_ns())
    urlpath = f"/0/private/{endpoint}"
    data["nonce"] = nonce
    headers = {
        "API-Key": KRAKEN_API_KEY,
        "API-Sign": kraken_signature(urlpath, data, KRAKEN_API_SECRET)
    }
    try:
        response = requests.post(KRAKEN_URL + urlpath, headers=headers, data=data, timeout=20)
        return response.json()
    except Exception as e:
        return {
            "error": [f"LOCAL: Kraken request failed: {e}"],
            "result": {}
        }
def get_btc_balance():
    result = kraken_private_request("Balance", {})
    try:
        return float(result.get("result", {}).get("XXBT", 0))
    except Exception:
        return 0.0
def get_json_volume(data, action, fallback=DEFAULT_BTC_VOLUME):
    if action in ["BTC_EXIT", "BTC_SELL", "SELL", "SELL / EXIT LONG"]:
        volume = (
            clean_value(data.get("sell_amount_btc"))
            or clean_value(data.get("max_sell_btc"))
            or clean_value(data.get("amount_btc"))
            or clean_value(data.get("volume"))
            or clean_value(data.get("qty"))
            or clean_value(data.get("quantity"))
            or fallback
        )
    else:
        volume = (
            clean_value(data.get("buy_amount_btc"))
            or clean_value(data.get("amount_btc"))
            or clean_value(data.get("volume"))
            or clean_value(data.get("qty"))
            or clean_value(data.get("quantity"))
            or fallback
        )
    try:
        volume_float = float(volume)
        if volume_float <= 0:
            return fallback
        return f"{volume_float:.8f}"
    except Exception:
        return fallback
def kraken_buy(volume):
    return kraken_private_request("AddOrder", {
        "ordertype": "market",
        "type": "buy",
        "volume": str(volume),
        "pair": PAIR
    })
def kraken_sell(volume):
    return kraken_private_request("AddOrder", {
        "ordertype": "market",
        "type": "sell",
        "volume": str(volume),
        "pair": PAIR
    })
def order_was_ok(result):
    errors = result.get("error", [])
    txid = result.get("result", {}).get("txid", [])
    return (not errors) and bool(txid)
def order_id_text(result):
    try:
        txid = result.get("result", {}).get("txid", [])
        if isinstance(txid, list):
            return ", ".join(txid)
        return str(txid)
    except Exception:
        return ""
def build_trade_result_text(data, action, price):
    reason = clean_value(data.get("exit_reason")) or clean_value(data.get("reason"))
    trade_buy = clean_value(data.get("trade_buy")) or clean_value(data.get("trade_entry_price")) or clean_value(data.get("entry_price"))
    trade_sell = clean_value(data.get("trade_sell")) or clean_value(data.get("trade_exit_price")) or clean_value(data.get("exit_price"))
    trade_points = clean_value(data.get("trade_points")) or clean_value(data.get("trade_gross_points")) or clean_value(data.get("trade_net_points"))
    trade_result = clean_value(data.get("trade_result_simple")) or clean_value(data.get("trade_result"))
    if trade_sell == "" and action in ["BTC_EXIT", "BTC_SELL", "SELL", "SELL / EXIT LONG"]:
        trade_sell = clean_value(price)
    if trade_points == "" and trade_buy != "" and trade_sell != "":
        try:
            trade_points = str(float(trade_sell) - float(trade_buy))
        except Exception:
            trade_points = ""
    if trade_result == "" and trade_points != "":
        try:
            p = float(trade_points)
            trade_result = "WIN" if p > 0 else "LOSS" if p < 0 else "FLAT"
        except Exception:
            trade_result = ""
    if action in ["BTC_BUY", "BUY", "BUY LONG", "RECLAIM BUY"]:
        return f"\nReden: {reason}\n" if reason != "" else ""
    if action in ["BTC_EXIT", "BTC_SELL", "SELL", "SELL / EXIT LONG"]:
        lines = []
        if trade_buy != "":
            lines.append(f"BUY: {fmt_number(trade_buy, 1)}")
        if trade_sell != "":
            lines.append(f"SELL: {fmt_number(trade_sell, 1)}")
        if trade_points != "":
            lines.append(f"TRADE: {fmt_points(trade_points)} punten")
        if trade_result != "":
            lines.append(f"RESULT: {trade_result}")
        if reason != "":
            lines.append(f"REASON: {reason}")
        if lines:
            return "\nTrade resultaat\n" + "\n".join(lines) + "\n"
    return ""
def is_supported_kraken_bot(bot, ticker, action, data):
    bot_upper = str(bot).upper()
    ticker_upper = str(ticker).upper()
    version_upper = str(data.get("v_version", "")).upper()
    strategy_upper = str(data.get("strategy_base", "")).upper()
    ticker_ok = ticker_upper in ["BTCEUR", "BTCEUR.P", "XBT/EUR", "XBTEUR"]
    action_ok = action in ["BTC_BUY", "BTC_EXIT"]
    bot_ok = (
        "RENE BTC SPOT BOT KRAKEN" in bot_upper
        or ("RBT" in bot_upper and "KRAKEN" in bot_upper)
        or "KRAKEN" in version_upper
        or "KRAKEN" in strategy_upper
    )
    return bot_ok and ticker_ok and action_ok
def env_live_allowed():
    return (
        TRADE_MODE_ENV == "KRAKEN_LIVE"
        or BOT_MODE_ENV == "KRAKEN_LIVE"
        or bool_value(EXECUTE_ORDERS_ENV)
        or bool_value(LIVE_TRADING_ENV)
    ) and not bool_value(DRY_RUN_ENV) and not bool_value(PAPER_TRADING_ENV) and not bool_value(TELEGRAM_ONLY_ENV)
def json_live_requested(data, trade_mode):
    return (
        bool_value(data.get("live"))
        or bool_value(data.get("is_live"))
        or bool_value(data.get("kraken_order"))
        or bool_value(data.get("place_order"))
        or bool_value(data.get("execute"))
        or bool_value(data.get("live_order"))
        or clean_value(trade_mode) == "KRAKEN_LIVE"
        or clean_value(data.get("mode")) == "KRAKEN_LIVE"
    ) and not bool_value(data.get("telegram_only")) and not bool_value(data.get("dry_run")) and not bool_value(data.get("paper"))
def build_blocked_message(data, base_message, reason):
    return base_message + f"""
⚠️ Kraken-order NIET uitgevoerd
Reden:
{reason}
Diagnose:
trade_mode_json: {clean_value(data.get("trade_mode"))}
mode_json: {clean_value(data.get("mode"))}
live_json: {clean_value(data.get("live"))}
execute_json: {clean_value(data.get("execute"))}
place_order_json: {clean_value(data.get("place_order"))}
kraken_order_json: {clean_value(data.get("kraken_order"))}
telegram_only_json: {clean_value(data.get("telegram_only"))}
dry_run_json: {clean_value(data.get("dry_run"))}
paper_json: {clean_value(data.get("paper"))}
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
"""
@app.route("/")
def home():
    return "Rene Kraken BTC Spot Bot draait."
@app.route("/send")
def send_test():
    send_telegram("TEST BERICHT VAN RENDER BOT")
    return "test gestuurd"
@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json or {}
    bot = clean_value(data.get("bot"))
    action = clean_value(data.get("action"))
    ticker = clean_value(data.get("ticker"))
    price = clean_value(data.get("price"))
    timeframe = clean_value(data.get("timeframe")) or clean_value(data.get("tf"))
    trade_mode = clean_value(data.get("trade_mode"))
    base_message = f"""Trading Alert
Bot: {bot}
Ticker: {ticker}
Actie: {action}
Prijs: {price}
Timeframe: {timeframe}
"""
    trade_text = build_trade_result_text(data, action, price)
    supported_kraken_bot = is_supported_kraken_bot(bot, ticker, action, data)
    live_requested = json_live_requested(data, trade_mode)
    live_allowed = env_live_allowed()
    if supported_kraken_bot and live_requested and live_allowed:
        order_volume = get_json_volume(data, action)
        try:
            order_volume_float = float(order_volume)
        except Exception:
            order_volume_float = 0.0
        if order_volume_float < MIN_BTC_VOLUME:
            message = build_blocked_message(
                data,
                base_message + trade_text,
                f"Ordervolume te laag: {order_volume}. Minimum is {MIN_BTC_VOLUME:.8f} BTC."
            )
            send_telegram(message)
            return "ok", 200
        if action == "BTC_BUY":
            result = kraken_buy(order_volume)
            if order_was_ok(result):
                message = f"""✅ Kraken BUY uitgevoerd
Bot: {bot}
Ticker: {ticker}
Prijs: {price}
Amount: {order_volume} BTC
Order ID: {order_id_text(result)}
Reden: {clean_value(data.get("reason"))}
"""
            else:
                message = base_message + trade_text + f"""
⚠️ Kraken BUY NIET uitgevoerd
Volume:
{order_volume}
Kraken result:
{result}
"""
            send_telegram(message)
            return "ok", 200
        if action == "BTC_EXIT":
            btc_balance = get_btc_balance()
            sell_volume = min(btc_balance, order_volume_float)
            if sell_volume < MIN_BTC_VOLUME:
                message = build_blocked_message(
                    data,
                    base_message + trade_text,
                    f"SELL genegeerd: onvoldoende BTC saldo voor bot-sell. BTC saldo: {btc_balance:.8f}, gevraagd: {order_volume}."
                )
                send_telegram(message)
                return "ok", 200
            result = kraken_sell(f"{sell_volume:.8f}")
            if order_was_ok(result):
                message = f"""✅ Kraken SELL uitgevoerd
Bot: {bot}
Ticker: {ticker}
Prijs: {price}
Amount: {sell_volume:.8f} BTC
Order ID: {order_id_text(result)}
""" + ("\n" + trade_text if trade_text else "")
            else:
                message = base_message + trade_text + f"""
⚠️ Kraken SELL NIET uitgevoerd
Volume:
{sell_volume:.8f}
Kraken result:
{result}
"""
            send_telegram(message)
            return "ok", 200
    if not supported_kraken_bot:
        message = build_blocked_message(
            data,
            base_message + trade_text,
            "Bot/ticker/action wordt niet herkend als ondersteunde Kraken BTC bot."
        )
        send_telegram(message)
        return "ok", 200
    if not live_requested:
        message = build_blocked_message(
            data,
            base_message + trade_text,
            "TradingView JSON vraagt geen live Kraken-order aan."
        )
        send_telegram(message)
        return "ok", 200
    if not live_allowed:
        message = build_blocked_message(
            data,
            base_message + trade_text,
            "Render environment staat live trading niet toe of dry-run/telegram-only staat nog aan."
        )
        send_telegram(message)
        return "ok", 200
    message = build_blocked_message(
        data,
        base_message + trade_text,
        "Onbekende blokkade."
    )
    send_telegram(message)
    return "ok", 200
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
`
