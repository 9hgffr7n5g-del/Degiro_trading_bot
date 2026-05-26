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

KRAKEN_URL = "https://api.kraken.com"
PAIR = "XBTEUR"
DEFAULT_BTC_VOLUME = "0.0001"


def send_telegram(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": CHAT_ID, "text": message})


def clean_value(value):
    if value is None:
        return ""
    value_str = str(value)
    if value_str.lower() in ["", "none", "null", "nan"]:
        return ""
    return value_str


def bool_value(value):
    if isinstance(value, bool):
        return value
    return str(value).lower() in ["true", "1", "yes", "ja"]


def fmt_number(value, decimals=1):
    value = clean_value(value)
    if value == "":
        return ""
    try:
        return f"{float(value):.{decimals}f}"
    except:
        return str(value)


def fmt_points(value):
    value = clean_value(value)
    if value == "":
        return ""
    try:
        number = float(value)
        sign = "+" if number > 0 else ""
        return f"{sign}{number:.1f}"
    except:
        return str(value)


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
        return f"\n\nReden: {reason}\n" if reason != "" else ""

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
            return "\n\nTrade resultaat\n" + "\n".join(lines)
    return ""


def kraken_signature(urlpath, data, secret):
    postdata = urllib.parse.urlencode(data)
    encoded = (str(data["nonce"]) + postdata).encode()
    message = urlpath.encode() + hashlib.sha256(encoded).digest()
    mac = hmac.new(base64.b64decode(secret), message, hashlib.sha512)
    return base64.b64encode(mac.digest()).decode()


def kraken_private_request(endpoint, data):
    nonce = str(int(time.time() * 1000))
    urlpath = f"/0/private/{endpoint}"
    data["nonce"] = nonce

    headers = {
        "API-Key": KRAKEN_API_KEY,
        "API-Sign": kraken_signature(urlpath, data, KRAKEN_API_SECRET)
    }

    response = requests.post(KRAKEN_URL + urlpath, headers=headers, data=data)
    return response.json()


def get_btc_balance():
    result = kraken_private_request("Balance", {})
    try:
        return float(result["result"].get("XXBT", 0))
    except Exception:
        return 0


def get_json_volume(data, fallback=DEFAULT_BTC_VOLUME):
    volume = (
        clean_value(data.get("amount_btc"))
        or clean_value(data.get("buy_amount_btc"))
        or clean_value(data.get("sell_amount_btc"))
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


@app.route("/")
def home():
    return "Bot + Kraken werkt!"


@app.route("/send")
def send_test():
    send_telegram("TEST BERICHT VAN RENDER BOT")
    return "test gestuurd"


@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json or {}

    bot = data.get("bot", "")
    action = data.get("action", "")
    ticker = data.get("ticker", "")
    price = data.get("price", "")
    timeframe = data.get("timeframe", "")
    trade_mode = data.get("trade_mode", "")

    live_requested = (
        bool_value(data.get("live"))
        or bool_value(data.get("kraken_order"))
        or bool_value(data.get("place_order"))
        or bool_value(data.get("execute"))
        or trade_mode == "KRAKEN_LIVE"
    )

    is_rene_btc_bot = (
        "Rene BTC Spot Bot Kraken" in bot
        and ticker in ["BTCEUR", "BTCEUR.P", "XBT/EUR", "XBTEUR"]
        and action in ["BTC_BUY", "BTC_EXIT"]
    )

    is_old_v5_bot = bot == "V5 BTC SPOT" and ticker == "BTCEUR"

    message = f"""
Trading Alert

Bot: {bot}
Ticker: {ticker}
Actie: {action}
Prijs: {price}
Timeframe: {timeframe}
"""
    message += build_trade_result_text(data, action, price)

    if is_rene_btc_bot and live_requested:
        btc_balance = get_btc_balance()
        order_volume = get_json_volume(data)

        message += f"""

BTC saldo: {btc_balance}
Order volume: {order_volume}
"""

        # V9.12.1:
        # BUY wordt NIET meer geblokkeerd door bestaand BTC-saldo.
        # Jij mag zelf BTC/EUR op Kraken hebben; de bot handelt zijn vaste order_volume.
        if action == "BTC_BUY":
            result = kraken_buy(order_volume)
            message += f"""

OK - Kraken BUY uitgevoerd

Volume:
{order_volume}

Resultaat:
{result}
"""

        # SELL verkoopt maximaal order_volume, nooit je hele BTC-saldo.
        elif action == "BTC_EXIT":
            sell_volume = min(btc_balance, float(order_volume))

            if sell_volume < 0.00001:
                message += """

LET OP - EXIT genegeerd
Geen BTC positie gevonden.
"""
            else:
                result = kraken_sell(f"{sell_volume:.8f}")
                message += f"""

OK - Kraken SELL uitgevoerd

Verkocht volume:
{sell_volume:.8f}

Resultaat:
{result}
"""

    elif is_old_v5_bot:
        btc_balance = get_btc_balance()
        message += f"""

BTC saldo: {btc_balance}
"""

        if action in ["BUY LONG", "RECLAIM BUY"]:
            if btc_balance > 0.00009:
                message += """

LET OP - BUY genegeerd
Er staat al BTC open.
Geen extra koop uitgevoerd.
"""
            else:
                result = kraken_buy(DEFAULT_BTC_VOLUME)
                message += f"""

OK - Kraken BUY uitgevoerd

Resultaat:
{result}
"""

        elif action == "SELL / EXIT LONG":
            if btc_balance < 0.00009:
                message += """

LET OP - EXIT genegeerd
Geen BTC positie gevonden.
"""
            else:
                result = kraken_sell(btc_balance)
                message += f"""

OK - Kraken SELL uitgevoerd

Verkocht volume:
{btc_balance}

Resultaat:
{result}
"""

        elif action == "FOMO BLOCK / NO BUY":
            message += """

FOMO BLOCK
Geen koop uitgevoerd.
"""

    else:
        message += """

INFO - Alleen Telegram-alert.
Geen Kraken-order uitgevoerd.
"""

    send_telegram(message)
    return "ok", 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
