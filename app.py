from flask import Flask
import requests
import os

app = Flask(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

@app.route("/")
def home():
    return "Bot werkt!"

@app.route("/send")
def send_message():
    message = "🚀 Rene Trading Bot werkt!"

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    data = {
        "chat_id": CHAT_ID,
        "text": message
    }

    requests.post(url, data=data)

    return "Bericht verzonden!"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
