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


@app.route("/")
def home():
    return "Bot + Kraken werkt!"


@app.route("/send")
def send_test():
    message = "🚀 TEST BERICHT VAN RENDER BOT"

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    payload = {
        "chat_id": CHAT_ID,
        "text": message
    }

    requests.post(url, data=payload)

    return "test gestuurd"


def kraken_signature(urlpath, data, secret):
    postdata = urllib.parse.urlencode(data)
    encoded = (str(data["nonce"]) + postdata).encode()
    message = urlpath.encode() + hashlib.sha256(encoded).digest()
    mac = hmac.new(base64.b64decode(secret), message, hashlib.sha512)
    sigdigest = base64.b64encode(mac.digest())

    return sigdigest.decode()


def kraken_buy_market():
    nonce = str(int(time.time() * 1000))
    urlpath = "/0/private/AddOrder"

    data = {
        "nonce": nonce,
        "ordertype": "market",
        "type": "buy",
        "volume": "0.0001",
        "pair": "XBTEUR"
    }

    headers = {
        "API-Key": KRAKEN_API_KEY,
        "API-Sign": kraken_signature(urlpath, data, KRAKEN_API_SECRET)
    }

    response = requests.post(
        KRAKEN_URL + urlpath,
        headers=headers,
        data=data
    )

    return response.text


@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json or {}

    action = data.get("action")

    message = f"""
🚀 Trading Alert

Ticker: {data.get('ticker')}
Actie: {action}
Prijs: {data.get('price')}
Timeframe: {data.get('timeframe')}
"""

    if action == "BUY LONG":
        kraken_result = kraken_buy_market()

        message += f"""

✅ Kraken BUY verstuurd
{kraken_result}
"""

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    payload = {
        "chat_id": CHAT_ID,
        "text": message
    }

    requests.post(url, data=payload)

    return "ok", 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)