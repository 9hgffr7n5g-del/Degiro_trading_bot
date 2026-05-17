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


def kraken_signature(urlpath, data, secret):

    postdata = urllib.parse.urlencode(data)

    encoded = (str(data["nonce"]) + postdata).encode()

    message = urlpath.encode() + hashlib.sha256(encoded).digest()

    mac = hmac.new(
        base64.b64decode(secret),
        message,
        hashlib.sha512
    )

    sigdigest = base64.b64encode(mac.digest())

    return sigdigest.decode()


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
        btc_balance = float(result["result"].get("XXBT", 0))
        return btc_balance

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

    action = data.get("action")
    ticker = data.get("ticker")
    price = data.get("price")
    timeframe = data.get("timeframe")

    btc_balance = get_btc_balance()

    message = f"""
🚀 Trading Alert

Ticker: {ticker}
Actie: {action}
Prijs: {price}
Timeframe: {timeframe}

BTC saldo: {btc_balance}
"""

    # BUY
    if action == "BUY LONG":

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

    # SELL
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

    send_telegram(message)

    return "ok", 200


if __name__ == "__main__":

    app.run(host="0.0.0.0", port=10000)