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
BTC_VOLUME = "0.0001"


def send_telegram(message):

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    payload = {
        "chat_id": CHAT_ID,
        "text": message
    }

    requests.post(url, data=payload)


def clean_value(value):
    if value is None:
        return ""
    value_str = str(value)
    if value_str.lower() in ["", "none", "null", "nan"]:
        return ""
    return value_str


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
    """
    Leest extra Pine/V9.11 velden uit voor Telegram.
    Laat alleen korte info zien:
    BUY, SELL, TRADE punten, RESULT en REASON.
    """

    reason = clean_value(data.get("exit_reason")) or clean_value(data.get("reason"))

    trade_buy = (
        clean_value(data.get("trade_buy"))
        or clean_value(data.get("trade_entry_price"))
        or clean_value(data.get("entry_price"))
    )

    trade_sell = (
        clean_value(data.get("trade_sell"))
        or clean_value(data.get("trade_exit_price"))
        or clean_value(data.get("exit_price"))
    )

    trade_points = (
        clean_value(data.get("trade_points"))
        or clean_value(data.get("trade_gross_points"))
        or clean_value(data.get("trade_net_points"))
    )

    trade_result = (
        clean_value(data.get("trade_result_simple"))
        or clean_value(data.get("trade_result"))
    )

    # Als Pine geen trade_sell meestuurt, gebruik huidige exit-prijs.
    if trade_sell == "" and action in ["BTC_EXIT", "BTC_SELL", "SELL", "SELL / EXIT LONG"]:
        trade_sell = clean_value(price)

    # Als punten ontbreken maar BUY en SELL bestaan, zelf berekenen.
    if trade_points == "" and trade_buy != "" and trade_sell != "":
        try:
            trade_points = str(float(trade_sell) - float(trade_buy))
        except:
            trade_points = ""

    # Als result ontbreekt, bepalen op basis van punten.
    if trade_result == "" and trade_points != "":
        try:
            points_float = float(trade_points)
            if points_float > 0:
                trade_result = "WIN"
            elif points_float < 0:
                trade_result = "LOSS"
            else:
                trade_result = "FLAT"
        except:
            trade_result = ""

    # Bij BUY alleen eventueel reason tonen, geen trade-resultaat.
    if action in ["BTC_BUY", "BUY", "BUY LONG", "RECLAIM BUY"]:
        if reason != "":
            return f"""

Reden: {reason}
"""
        return ""

    # Bij EXIT/SELL trade-resultaat tonen.
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

        if len(lines) > 0:
            return """

📊 Trade resultaat
""" + "\n".join(lines)

    return ""


def kraken_signature(urlpath, data, secret):

    postdata = urllib.parse.urlencode(data)

    encoded = (str(data["nonce"]) + postdata).encode()

    message = urlpath.encode() + hashlib.sha256(encoded).digest()

    mac = hmac.new(
        base64.b64decode(secret),
        message,
        hashlib.sha512
    )

    return base64.b64encode(mac.digest()).decode()


def kraken_private_request(endpoint, data):

    nonce = str(int(time.time() * 1000))

    urlpath = f"/0/private/{endpoint}"

    data["nonce"] = nonce

    headers = {
        "API-Key": KRAKEN_API_KEY,
        "API-Sign": kraken_signature(
            urlpath,
            data,
            KRAKEN_API_SECRET
        )
    }

    response = requests.post(
        KRAKEN_URL + urlpath,
        headers=headers,
        data=data
    )

    return response.json()


def get_btc_balance():

    result = kraken_private_request("Balance", {})

    try:
        return float(result["result"].get("XXBT", 0))

    except:
        return 0


def kraken_buy():

    data = {
        "ordertype": "market",
        "type": "buy",
        "volume": BTC_VOLUME,
        "pair": PAIR
    }

    return kraken_private_request("AddOrder", data)


def kraken_sell(volume):

    data = {
        "ordertype": "market",
        "type": "sell",
        "volume": str(volume),
        "pair": PAIR
    }

    return kraken_private_request("AddOrder", data)


@app.route("/")
def home():

    return "Bot + Kraken werkt!"


@app.route("/send")
def send_test():

    send_telegram("🚀 TEST BERICHT VAN RENDER BOT")

    return "test gestuurd"


@app.route("/webhook", methods=["POST"])
def webhook():

    data = request.json or {}

    bot = data.get("bot", "")
    action = data.get("action", "")
    ticker = data.get("ticker", "")
    price = data.get("price", "")
    timeframe = data.get("timeframe", "")

    is_btc_bot = bot == "V5 BTC SPOT" and ticker == "BTCEUR"

    message = f"""
🚀 Trading Alert

Bot: {bot}
Ticker: {ticker}
Actie: {action}
Prijs: {price}
Timeframe: {timeframe}
"""

    # Extra korte trade-info voor V9.11 / Pine JSON.
    message += build_trade_result_text(data, action, price)

    if is_btc_bot:

        btc_balance = get_btc_balance()

        message += f"""

BTC saldo: {btc_balance}
"""

        # === BUY / RECLAIM BUY ===
        if action in ["BUY LONG", "RECLAIM BUY"]:

            if btc_balance > 0.00009:

                message += """

⚠️ BUY genegeerd
Er staat al BTC open.
Geen extra koop uitgevoerd.
"""

            else:

                result = kraken_buy()

                message += f"""

✅ Kraken BUY uitgevoerd

Resultaat:
{result}
"""

        # === SELL ===
        elif action == "SELL / EXIT LONG":

            if btc_balance < 0.00009:

                message += """

⚠️ EXIT genegeerd
Geen BTC positie gevonden.
"""

            else:

                result = kraken_sell(btc_balance)

                message += f"""

✅ Kraken SELL uitgevoerd

Verkocht volume:
{btc_balance}

Resultaat:
{result}
"""

        # === FOMO ===
        elif action == "FOMO BLOCK / NO BUY":

            message += """

⛔ FOMO BLOCK
Geen koop uitgevoerd.
"""

    else:

        message += """

ℹ️ Alleen Telegram-alert.
Geen Kraken-order uitgevoerd.
"""

    send_telegram(message)

    return "ok", 200


if __name__ == "__main__":

    app.run(host="0.0.0.0", port=10000)
