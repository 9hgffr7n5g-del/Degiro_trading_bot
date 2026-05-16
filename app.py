from flask import Flask, request
import requests
import os

app = Flask(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

@app.route("/")
def home():
    return "Bot werkt!"

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json

    message = f"""
🚀 Trading Alert

Ticker: {data.get('ticker')}
Actie: {data.get('action')}
Prijs: {data.get('price')}
Timeframe: {data.get('timeframe')}
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
