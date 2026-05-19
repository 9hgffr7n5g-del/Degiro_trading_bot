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
