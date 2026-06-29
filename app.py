from flask import Flask, request, jsonify
import os
import time
import json
import hmac
import base64
import hashlib
import urllib.parse
import requests
from threading import Lock, Thread
from datetime import datetime, timedelta, timezone
try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None

app = Flask(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

# V9.35: gescheiden Telegram routes voor BTC Trendbot live en BTC Scalpbot paper.
# Backwards compatible:
# - CHAT_ID blijft de standaard / Trend live chat.
# - Zet BTC_SCALP_CHAT_ID of SCALP_CHAT_ID voor aparte Scalp Telegram-chat.
TREND_BOT_TOKEN = os.environ.get("BTC_TREND_BOT_TOKEN") or os.environ.get("TREND_BOT_TOKEN") or BOT_TOKEN
TREND_CHAT_ID = os.environ.get("BTC_TREND_CHAT_ID") or os.environ.get("TREND_CHAT_ID") or CHAT_ID
SCALP_BOT_TOKEN = os.environ.get("BTC_SCALP_BOT_TOKEN") or os.environ.get("SCALP_BOT_TOKEN") or BOT_TOKEN
SCALP_CHAT_ID = os.environ.get("BTC_SCALP_CHAT_ID") or os.environ.get("SCALP_CHAT_ID") or CHAT_ID
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


def env_bval(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ["true", "1", "yes", "ja", "on"]

# Afgesproken inzet: full BTC order 0.00400 BTC, max botpositie 0.00400 BTC.
# Render env mag dit overschrijven met DEFAULT_BTC_VOLUME of BUY_ORDER_SIZE.
DEFAULT_BTC_VOLUME = os.environ.get("BUY_ORDER_SIZE") or os.environ.get("DEFAULT_BTC_VOLUME", "0.00400")
MIN_BTC_VOLUME = float(os.environ.get("MIN_BTC_VOLUME", "0.00010"))
MAX_BOT_POSITION_BTC = float(os.environ.get("MAX_BOT_POSITION_BTC", "0.00400"))
# Veiligheid: standaard bepaalt Render de BUY-grootte, ook als Pine nog 0.00010 meestuurt.
# Zet HONOR_TV_BUY_VOLUME=true als je later juist de TradingView JSON leidend wilt maken.
HONOR_TV_BUY_VOLUME = env_bval(os.environ.get("HONOR_TV_BUY_VOLUME", "false"))

# Zet op Render bij voorkeur:
# BOT_STATE_FILE=/data/bot_state.json
# TRADE_LOG_FILE=/data/trades.json
# APP_TZ=Europe/Amsterdam
STATE_FILE = os.environ.get("BOT_STATE_FILE", "/data/bot_state.json" if os.path.isdir("/data") else "bot_state.json")
TRADE_LOG_FILE = os.environ.get("TRADE_LOG_FILE", "/data/trades.json" if os.path.isdir("/data") else "trades.json")
APP_TZ = os.environ.get("APP_TZ", "Europe/Amsterdam")
ROUND_TRIP_COST_POINTS = float(os.environ.get("ROUND_TRIP_COST_POINTS", "0.0"))

# BTC Dual 1 Scalp paper engine - geen Kraken-orders.
# Scalp wordt apart gemeten naast de live Trend/Kraken positie.
BTC_SCALP_PAPER_ENABLED = env_bval(os.environ.get("BTC_SCALP_PAPER_ENABLED", "true"))
BTC_SCALP_STATE_FILE = os.environ.get("BTC_SCALP_STATE_FILE", "/data/btc_scalp_state.json" if os.path.isdir("/data") else "btc_scalp_state.json")
BTC_SCALP_LOG_FILE = os.environ.get("BTC_SCALP_LOG_FILE", "/data/btc_scalp_trades.json" if os.path.isdir("/data") else "btc_scalp_trades.json")
BTC_SCALP_PAPER_AMOUNT_BTC = float(os.environ.get("BTC_SCALP_PAPER_AMOUNT_BTC", "0.00400"))
BTC_SCALP_TP_POINTS = float(os.environ.get("BTC_SCALP_TP_POINTS", "250"))
BTC_SCALP_TRAIL_ENABLED = env_bval(os.environ.get("BTC_SCALP_TRAIL_ENABLED", "true"))
BTC_SCALP_TRAIL_TRIGGER_POINTS = float(os.environ.get("BTC_SCALP_TRAIL_TRIGGER_POINTS", str(BTC_SCALP_TP_POINTS)))
BTC_SCALP_TRAIL_STEP_POINTS = float(os.environ.get("BTC_SCALP_TRAIL_STEP_POINTS", "50"))
BTC_SCALP_EARLY_LOCK_ENABLED = env_bval(os.environ.get("BTC_SCALP_EARLY_LOCK_ENABLED", "true"))
BTC_SCALP_EARLY_LOCK_TRIGGER_POINTS = float(os.environ.get("BTC_SCALP_EARLY_LOCK_TRIGGER_POINTS", "150"))
BTC_SCALP_EARLY_LOCK_FLOOR_POINTS = float(os.environ.get("BTC_SCALP_EARLY_LOCK_FLOOR_POINTS", "75"))
BTC_SCALP_SEND_TELEGRAM = env_bval(os.environ.get("BTC_SCALP_SEND_TELEGRAM", "true"))


# TURBOBOT PAPER SETTINGS - geen echte brokerorders.
# Let op: deze settings blijven apart van BTC/Kraken live.
TURBOBOT_STATE_FILE = os.environ.get("TURBOBOT_STATE_FILE", "/data/turbobot_state.json" if os.path.isdir("/data") else "turbobot_state.json")
TURBOBOT_LOG_FILE = os.environ.get("TURBOBOT_LOG_FILE", "/data/turbobot_trades.json" if os.path.isdir("/data") else "turbobot_trades.json")
TURBOBOT_START_CAPITAL = float(os.environ.get("TURBOBOT_START_CAPITAL", "10000"))
TURBOBOT_TRADE_FRACTION = float(os.environ.get("TURBOBOT_TRADE_FRACTION", "0.25"))
TURBOBOT_LEVERAGE = float(os.environ.get("TURBOBOT_LEVERAGE", "4"))
TURBOBOT_DAILY_TARGET_PCT = float(os.environ.get("TURBOBOT_DAILY_TARGET_PCT", "1.0"))
TURBOBOT_DAILY_STOP_PCT = float(os.environ.get("TURBOBOT_DAILY_STOP_PCT", "-1.0"))
# V9.26: dagtarget is geen harde winst-max meer.
# Target hit blijft zichtbaar in rapport/status, maar Turbobot mag door in runner/profit-protect mode.
# Alleen dagstop/kill switch/cooldown/max trades kunnen nog echt blokkeren.
TURBOBOT_DAILY_TARGET_BLOCKS_NEW_TRADES = env_bval(os.environ.get("TURBOBOT_DAILY_TARGET_BLOCKS_NEW_TRADES", "false"))
TURBOBOT_RUNNER_MODE_AFTER_TARGET = env_bval(os.environ.get("TURBOBOT_RUNNER_MODE_AFTER_TARGET", "true"))
TURBOBOT_MAX_TRADES_PER_DAY = int(os.environ.get("TURBOBOT_MAX_TRADES_PER_DAY", "12"))
TURBOBOT_COOLDOWN_AFTER_LOSS_SEC = int(os.environ.get("TURBOBOT_COOLDOWN_AFTER_LOSS_SEC", "300"))
# V9.27: Telegram schoonhouden. Pine mag blijven sturen; Render negeert ruis stil.
TURBOBOT_SILENCE_AFTER_DAILY_STOP = env_bval(os.environ.get("TURBOBOT_SILENCE_AFTER_DAILY_STOP", "true"))
TURBOBOT_SILENCE_IGNORED_FLAT_EXITS = env_bval(os.environ.get("TURBOBOT_SILENCE_IGNORED_FLAT_EXITS", "true"))
TURBOBOT_SILENCE_BAD_LOCKS = env_bval(os.environ.get("TURBOBOT_SILENCE_BAD_LOCKS", "true"))
TURBOBOT_LOCK_REQUIRES_PROFIT = env_bval(os.environ.get("TURBOBOT_LOCK_REQUIRES_PROFIT", "true"))
TURBOBOT_LOCK_MIN_OPEN_PCT = float(os.environ.get("TURBOBOT_LOCK_MIN_OPEN_PCT", "0.0"))


# AUTO DAILY TELEGRAM REPORTS - Render-side, Pine blijft leeg.
# BTC/Kraken live dagoverzicht om 22:02 Europe/Amsterdam.
# Turbobot/LEV paper dagoverzicht om 22:15 Europe/Amsterdam.
AUTO_DAILY_REPORTS_ENABLED = env_bval(os.environ.get("AUTO_DAILY_REPORTS_ENABLED", "true"))
BTC_DAILY_REPORT_HOUR = int(os.environ.get("BTC_DAILY_REPORT_HOUR", "22"))
BTC_DAILY_REPORT_MINUTE = int(os.environ.get("BTC_DAILY_REPORT_MINUTE", "2"))
TURBOBOT_DAILY_REPORT_HOUR = int(os.environ.get("TURBOBOT_DAILY_REPORT_HOUR", "22"))
TURBOBOT_DAILY_REPORT_MINUTE = int(os.environ.get("TURBOBOT_DAILY_REPORT_MINUTE", "15"))
AUTO_DAILY_REPORT_CHECK_SEC = int(os.environ.get("AUTO_DAILY_REPORT_CHECK_SEC", "20"))
AUTO_DAILY_REPORT_WINDOW_MIN = int(os.environ.get("AUTO_DAILY_REPORT_WINDOW_MIN", "3"))
AUTO_SUMMARY_STATE_FILE = os.environ.get("AUTO_SUMMARY_STATE_FILE", "/data/auto_summary_state.json" if os.path.isdir("/data") else "auto_summary_state.json")


# Render-side chop/loss guard. Pine is already close to TradingView limits,
# so this safety layer blocks only normal BUY signals after a bad streak/day loss.
LOSS_GUARD_ENABLED = env_bval(os.environ.get("LOSS_GUARD_ENABLED", "true"))
LOSS_STREAK_LIMIT = int(os.environ.get("LOSS_STREAK_LIMIT", "3"))
LOSS_GUARD_DAILY_LIMIT_EUR = float(os.environ.get("LOSS_GUARD_DAILY_LIMIT_EUR", "-15"))
LOSS_GUARD_COOLDOWN_CANDLES = int(os.environ.get("LOSS_GUARD_COOLDOWN_CANDLES", "6"))
LOSS_GUARD_TIMEFRAME_MIN = int(os.environ.get("LOSS_GUARD_TIMEFRAME_MIN", "5"))
LOSS_GUARD_ALLOW_QUALITY_OVERRIDE = env_bval(os.environ.get("LOSS_GUARD_ALLOW_QUALITY_OVERRIDE", "true"))
QUALITY_OVERRIDE_KEYWORDS = [
    x.strip().upper()
    for x in os.environ.get(
        "QUALITY_OVERRIDE_KEYWORDS",
        "ROCKET,BREAKOUT,RECLAIM,HH,HL,HHHL,TREND,SUPPORT,BOUNCE,STRONG,RECOVERY"
    ).split(",")
    if x.strip()
]

STATE_LOCK = Lock()
TRADE_LOCK = Lock()
TURBOBOT_LOCK = Lock()
TURBOBOT_LOG_LOCK = Lock()
BTC_SCALP_LOCK = Lock()
BTC_SCALP_LOG_LOCK = Lock()
AUTO_SUMMARY_LOCK = Lock()

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
    "last_trade_net_points_est": None,
    "last_trade_net_eur_est": None,
    "last_trade_result": None,
    "last_trade_entry_price": None,
    "last_trade_exit_price": None,
    "last_trade_volume": None,
    "last_pine_entry_price": None,
    "last_pine_entry_diff": None,
    "last_pine_result_warning": "",
    "last_telegram_ok": None,
    "last_telegram_error": "",
    "last_telegram_ts": None,
    "server_started_ts": int(time.time()),
    "loss_guard_until_ts": 0,
    "loss_guard_reason": "",
    "loss_guard_last_arm_ts": 0,
    "loss_guard_last_block_ts": 0,
    "loss_guard_last_override_ts": 0
}




DEFAULT_BTC_SCALP_STATE = {
    "position": "FLAT",
    "entry_price": None,
    "entry_ts": None,
    "entry_reason": "",
    "entry_signal": "",
    "amount_btc": BTC_SCALP_PAPER_AMOUNT_BTC,
    "last_price": None,
    "last_action": None,
    "last_update_ts": None,
    "last_closed_points": 0.0,
    "last_closed_eur": 0.0,
    "last_closed_result": "",
    "max_profit_points": None,
    "trail_active": False,
    "trail_floor_points": None,
    "trail_trigger_points": BTC_SCALP_TRAIL_TRIGGER_POINTS,
    "trail_step_points": BTC_SCALP_TRAIL_STEP_POINTS,
    "early_lock_active": False,
    "early_lock_trigger_points": BTC_SCALP_EARLY_LOCK_TRIGGER_POINTS,
    "early_lock_floor_points": BTC_SCALP_EARLY_LOCK_FLOOR_POINTS,
    "daily_date": "",
    "daily_closed_trades": 0,
    "daily_wins": 0,
    "daily_losses": 0,
    "daily_flats": 0,
    "daily_points": 0.0,
    "daily_eur": 0.0,
    "server_started_ts": int(time.time())
}


DEFAULT_TURBOBOT_STATE = {
    "capital": TURBOBOT_START_CAPITAL,
    "start_capital": TURBOBOT_START_CAPITAL,
    "position": "FLAT",
    "symbol": "",
    "timeframe": "",
    "entry_price": None,
    "entry_ts": None,
    "entry_signal": "",
    "entry_reason": "",
    "trade_size_eur": None,
    "leverage": TURBOBOT_LEVERAGE,
    "last_signal": "",
    "last_price": None,
    "last_update_ts": None,
    "last_closed_pnl_eur": 0.0,
    "last_closed_pnl_pct": 0.0,
    "last_closed_result": "",
    "last_closed_side": "",
    "last_closed_reason": "",
    "daily_date": "",
    "daily_realized_eur": 0.0,
    "daily_realized_pct": 0.0,
    "daily_closed_trades": 0,
    "daily_wins": 0,
    "daily_losses": 0,
    "daily_flats": 0,
    "daily_longs": 0,
    "daily_shorts": 0,
    "daily_locks": 0,
    "daily_target_hit": False,
    "daily_stop_hit": False,
    "cooldown_until_ts": 0,
    "kill_switch": False,
    "server_started_ts": int(time.time())
}


def ensure_parent(path):
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)


def now_ts():
    return int(time.time())


def local_dt(ts=None):
    if ts is None:
        ts = now_ts()
    if ZoneInfo:
        try:
            return datetime.fromtimestamp(int(ts), ZoneInfo(APP_TZ))
        except Exception:
            pass
    return datetime.fromtimestamp(int(ts), timezone.utc)


def local_date_str(ts=None):
    return local_dt(ts).strftime("%Y-%m-%d")


def local_time_str(ts=None):
    return local_dt(ts).strftime("%d-%m-%Y %H:%M:%S")


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



def webhook_payload():
    """Lees TradingView webhooks robuust.
    Sommige alerts komen als application/json binnen, andere als text/plain met JSON-tekst.
    BTC/Kraken en Turbobot gebruiken dezelfde webhook, dus dit moet vÃ³Ã³r route-splitsing gebeuren.
    """
    data = request.get_json(silent=True)
    if isinstance(data, dict):
        return data

    raw = request.get_data(as_text=True) or ""
    raw = raw.strip()
    if not raw:
        return {}

    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    return {"message": raw, "raw_text": raw}


def fmt(value, decimals=1):
    try:
        return f"{float(value):.{decimals}f}"
    except Exception:
        return str(value)


def fmt_eur(value, decimals=4):
    try:
        x = float(value)
        if x > 0:
            return f"+EUR {x:.{decimals}f}"
        if x < 0:
            return f"-EUR {abs(x):.{decimals}f}"
        return f"EUR {x:.{decimals}f}"
    except Exception:
        return str(value)


def fmt_eur_abs(value, decimals=2):
    try:
        x = float(value)
        return f"EUR {x:.{decimals}f}"
    except Exception:
        return str(value)


def fmt_pct(value, decimals=2):
    try:
        x = float(value)
        sign = "+" if x > 0 else ""
        return f"{sign}{x:.{decimals}f}%"
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


def result_from_eur(eur_value):
    p = fval(eur_value, 0.0)
    if p > 0:
        return "WIN"
    if p < 0:
        return "LOSS"
    return "FLAT"


def nl_result(result):
    r = clean(result).upper()
    if r == "WIN":
        return "WIN"
    if r == "LOSS":
        return "LOSS"
    return "FLAT"


def update_telegram_state(ok, error=""):
    try:
        s = load_state()
        s["last_telegram_ok"] = bool(ok)
        s["last_telegram_error"] = clean(error)
        s["last_telegram_ts"] = now_ts()
        save_state(s)
    except Exception as e:
        print("Telegram state update error:", e)


def telegram_target(route="default"):
    r = clean(route).lower()
    if r in ["scalp", "btc_scalp", "btc_scalpbot", "paper"]:
        return SCALP_BOT_TOKEN, SCALP_CHAT_ID, "BTC_SCALP"
    if r in ["trend", "btc_trend", "btc_trendbot", "live"]:
        return TREND_BOT_TOKEN, TREND_CHAT_ID, "BTC_TREND"
    return BOT_TOKEN, CHAT_ID, "DEFAULT"


def send_telegram(message, route="default"):
    token, chat_id, route_name = telegram_target(route)
    if not token or not chat_id:
        print(f"Telegram not configured for {route_name}")
        print(message)
        update_telegram_state(False, f"Telegram config ontbreekt voor {route_name}")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        r = requests.post(url, data={"chat_id": chat_id, "text": message}, timeout=15)
        if 200 <= r.status_code < 300:
            update_telegram_state(True, "")
            return True
        err = f"{route_name} HTTP {r.status_code}: {r.text[:300]}"
        print("Telegram error:", err)
        update_telegram_state(False, err)
        return False
    except Exception as e:
        err = f"{route_name} {str(e)}"
        print("Telegram error:", err)
        update_telegram_state(False, err)
        return False


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
        try:
            ensure_parent(STATE_FILE)
            state["last_update_ts"] = now_ts()
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            print("State save error:", e)
            raise


def reset_state():
    s = DEFAULT_STATE.copy()
    s["server_started_ts"] = now_ts()
    save_state(s)
    return s


def load_trades():
    with TRADE_LOCK:
        try:
            if os.path.exists(TRADE_LOG_FILE):
                with open(TRADE_LOG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    return data
        except Exception as e:
            print("Trade log load error:", e)
        return []


def save_trades(trades):
    with TRADE_LOCK:
        ensure_parent(TRADE_LOG_FILE)
        with open(TRADE_LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(trades, f, indent=2)


def append_trade_event(event):
    trades = load_trades()
    event = dict(event)
    event.setdefault("ts", now_ts())
    event.setdefault("tijd", local_time_str(event.get("ts")))
    event.setdefault("datum", local_date_str(event.get("ts")))
    trades.append(event)
    save_trades(trades)
    return event




def btc_scalp_load_state():
    with BTC_SCALP_LOCK:
        try:
            if os.path.exists(BTC_SCALP_STATE_FILE):
                with open(BTC_SCALP_STATE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                st = DEFAULT_BTC_SCALP_STATE.copy()
                if isinstance(data, dict):
                    st.update(data)
                return st
        except Exception as e:
            print("BTC scalp state load error:", e)
        return DEFAULT_BTC_SCALP_STATE.copy()


def btc_scalp_save_state(state):
    with BTC_SCALP_LOCK:
        ensure_parent(BTC_SCALP_STATE_FILE)
        state["last_update_ts"] = now_ts()
        with open(BTC_SCALP_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)


def btc_scalp_reset_state():
    st = DEFAULT_BTC_SCALP_STATE.copy()
    st["server_started_ts"] = now_ts()
    btc_scalp_save_state(st)
    return st


def btc_scalp_load_log():
    with BTC_SCALP_LOG_LOCK:
        try:
            if os.path.exists(BTC_SCALP_LOG_FILE):
                with open(BTC_SCALP_LOG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    return data
        except Exception as e:
            print("BTC scalp log load error:", e)
        return []


def btc_scalp_save_log(events):
    with BTC_SCALP_LOG_LOCK:
        ensure_parent(BTC_SCALP_LOG_FILE)
        with open(BTC_SCALP_LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(events, f, indent=2)


def btc_scalp_append_event(event):
    events = btc_scalp_load_log()
    event = dict(event)
    event.setdefault("ts", now_ts())
    event.setdefault("tijd", local_time_str(event.get("ts")))
    event.setdefault("datum", local_date_str(event.get("ts")))
    events.append(event)
    btc_scalp_save_log(events)
    return event


def btc_scalp_closed_events(start_ts=None, end_ts=None):
    out = []
    for e in btc_scalp_load_log():
        if e.get("type") != "BTC_SCALP_CLOSED_TRADE":
            continue
        ts = int(e.get("ts") or 0)
        if start_ts is not None and ts < start_ts:
            continue
        if end_ts is not None and ts >= end_ts:
            continue
        out.append(e)
    return out


def btc_scalp_summarize(events):
    total = len(events)
    wins = sum(1 for e in events if clean(e.get("result")).upper() == "WIN")
    losses = sum(1 for e in events if clean(e.get("result")).upper() == "LOSS")
    flats = total - wins - losses
    points = sum(fval(e.get("points"), 0.0) for e in events)
    eur = sum(fval(e.get("gross_eur"), 0.0) for e in events)
    best = max(events, key=lambda e: fval(e.get("points"), 0.0), default=None)
    worst = min(events, key=lambda e: fval(e.get("points"), 0.0), default=None)
    winrate = (wins / total * 100.0) if total else 0.0
    return {"closed_trades": total, "wins": wins, "losses": losses, "flats": flats, "winrate": winrate, "points": points, "eur": eur, "best": best, "worst": worst}


def btc_scalp_exit_message(price, reason, st, entry_price, points, eur, result):
    return f"""BTC SCALP PAPER SELL
Geen Kraken-order uitgevoerd.
Ticker: BTCEUR
Koers verkoop: {fmt(price, 1)}
Paper BTC: {BTC_SCALP_PAPER_AMOUNT_BTC:.8f}
Entry: {fmt(entry_price, 1)}
Punten: {pts(points)}
Resultaat: {result}
Paper EUR: {fmt_eur(eur)}
Reden: {reason}

Scalp positie: {st.get('position')}
Tijd: {local_time_str()}
"""


def btc_scalp_open_message(price, reason, st):
    return f"""BTC SCALP PAPER BUY
Geen Kraken-order uitgevoerd.
Ticker: BTCEUR
Koers: {fmt(price, 1)}
Paper BTC: {BTC_SCALP_PAPER_AMOUNT_BTC:.8f}
Early lock: +{BTC_SCALP_EARLY_LOCK_TRIGGER_POINTS:.0f} -> floor +{BTC_SCALP_EARLY_LOCK_FLOOR_POINTS:.0f}
Trail trigger: +{BTC_SCALP_TRAIL_TRIGGER_POINTS:.0f} punten
Trail stap: {BTC_SCALP_TRAIL_STEP_POINTS:.0f} punten
Reden: {reason}

Scalp positie: {st.get('position')}
Tijd: {local_time_str()}
"""


def btc_scalp_close(st, price, reason, action_label="SELL"):
    entry = fval(st.get("entry_price"), None)
    exitp = fval(price, None)
    if entry is None or exitp is None:
        return st, None
    points = exitp - entry
    eur = points * BTC_SCALP_PAPER_AMOUNT_BTC
    result = result_from_points(points)
    st["position"] = "FLAT"
    st["last_price"] = exitp
    st["last_action"] = action_label
    st["last_closed_points"] = points
    st["last_closed_eur"] = eur
    st["last_closed_result"] = result
    st["entry_price"] = None
    st["entry_ts"] = None
    st["entry_reason"] = ""
    st["entry_signal"] = ""
    st["max_profit_points"] = None
    st["trail_active"] = False
    st["trail_floor_points"] = None
    st["early_lock_active"] = False
    st["daily_date"] = local_date_str()
    st["daily_closed_trades"] = int(st.get("daily_closed_trades", 0) or 0) + 1
    if result == "WIN":
        st["daily_wins"] = int(st.get("daily_wins", 0) or 0) + 1
    elif result == "LOSS":
        st["daily_losses"] = int(st.get("daily_losses", 0) or 0) + 1
    else:
        st["daily_flats"] = int(st.get("daily_flats", 0) or 0) + 1
    st["daily_points"] = fval(st.get("daily_points"), 0.0) + points
    st["daily_eur"] = fval(st.get("daily_eur"), 0.0) + eur
    btc_scalp_save_state(st)
    btc_scalp_append_event({
        "type": "BTC_SCALP_CLOSED_TRADE",
        "entry_price": entry,
        "exit_price": exitp,
        "amount_btc": BTC_SCALP_PAPER_AMOUNT_BTC,
        "points": points,
        "gross_eur": eur,
        "result": result,
        "reason": reason,
        "action_label": action_label,
        "max_profit_points": st.get("max_profit_points"),
        "early_lock_active": st.get("early_lock_active"),
        "early_lock_floor_points": st.get("early_lock_floor_points"),
        "trail_floor_points": st.get("trail_floor_points")
    })
    return st, btc_scalp_exit_message(exitp, reason, st, entry, points, eur, result)


def handle_btc_scalp_paper(data):
    if not BTC_SCALP_PAPER_ENABLED:
        return None
    action = clean(data.get("action"))
    if action not in ["BTC_BUY", "BTC_EXIT", "BTC_SCALP_BUY", "BTC_SCALP_EXIT"]:
        return None
    if not bval(data.get("scalp_signal")) and clean(data.get("scalp_mode")).upper() != "PAPER":
        return None

    price = fval(data.get("price"), None)
    if price is None:
        return None
    reason = clean(data.get("exit_reason")) or clean(data.get("reason")) or action
    st = btc_scalp_load_state()
    st["last_price"] = price

    # Scalp trailing profit ladder. Geen Kraken-order, alleen paper.
    # Vanaf +250 wordt winst bewaakt. Daarna schuift de floor per 50 punten mee omhoog.
    if st.get("position") == "LONG":
        entry = fval(st.get("entry_price"), None)
        if entry is not None:
            current_points = price - entry
            max_points = fval(st.get("max_profit_points"), current_points)
            if current_points > max_points:
                max_points = current_points
            st["max_profit_points"] = max_points

            if (BTC_SCALP_EARLY_LOCK_ENABLED
                    and max_points >= BTC_SCALP_EARLY_LOCK_TRIGGER_POINTS
                    and max_points < BTC_SCALP_TRAIL_TRIGGER_POINTS):
                st["early_lock_active"] = True
                st["early_lock_floor_points"] = BTC_SCALP_EARLY_LOCK_FLOOR_POINTS
                if current_points < BTC_SCALP_EARLY_LOCK_FLOOR_POINTS:
                    st, msg = btc_scalp_close(st, price, f"SCALP EARLY LOCK SELL floor +{BTC_SCALP_EARLY_LOCK_FLOOR_POINTS:.0f} punten; max +{max_points:.1f} punten", "EARLY_LOCK")
                    if msg and BTC_SCALP_SEND_TELEGRAM:
                        send_telegram(msg, route="scalp")
                    return {"event": "scalp_early_lock_sell", "position": st.get("position"), "floor_points": BTC_SCALP_EARLY_LOCK_FLOOR_POINTS, "max_points": max_points}

            if BTC_SCALP_TRAIL_ENABLED and max_points >= BTC_SCALP_TRAIL_TRIGGER_POINTS:
                step = BTC_SCALP_TRAIL_STEP_POINTS if BTC_SCALP_TRAIL_STEP_POINTS > 0 else 50.0
                steps_above = int((max_points - BTC_SCALP_TRAIL_TRIGGER_POINTS) // step)
                floor_points = BTC_SCALP_TRAIL_TRIGGER_POINTS + (steps_above * step)
                st["trail_active"] = True
                st["trail_floor_points"] = floor_points

                if current_points < floor_points:
                    st, msg = btc_scalp_close(st, price, f"SCALP TRAIL SELL floor +{floor_points:.0f} punten; max +{max_points:.1f} punten", "TRAIL")
                    if msg and BTC_SCALP_SEND_TELEGRAM:
                        send_telegram(msg, route="scalp")
                    return {"event": "scalp_trail_sell", "position": st.get("position"), "floor_points": floor_points, "max_points": max_points}

            btc_scalp_save_state(st)

    if action in ["BTC_BUY", "BTC_SCALP_BUY"]:
        if st.get("position") == "LONG":
            btc_scalp_save_state(st)
            return {"event": "scalp_buy_ignored_already_long", "position": "LONG"}
        st["position"] = "LONG"
        st["entry_price"] = price
        st["entry_ts"] = now_ts()
        st["entry_reason"] = reason
        st["entry_signal"] = action
        st["amount_btc"] = BTC_SCALP_PAPER_AMOUNT_BTC
        st["max_profit_points"] = 0.0
        st["trail_active"] = False
        st["trail_floor_points"] = None
        st["trail_trigger_points"] = BTC_SCALP_TRAIL_TRIGGER_POINTS
        st["trail_step_points"] = BTC_SCALP_TRAIL_STEP_POINTS
        st["early_lock_active"] = False
        st["early_lock_trigger_points"] = BTC_SCALP_EARLY_LOCK_TRIGGER_POINTS
        st["early_lock_floor_points"] = BTC_SCALP_EARLY_LOCK_FLOOR_POINTS
        st["last_action"] = "BUY"
        st["daily_date"] = local_date_str()
        btc_scalp_save_state(st)
        btc_scalp_append_event({
            "type": "BTC_SCALP_OPEN",
            "entry_price": price,
            "amount_btc": BTC_SCALP_PAPER_AMOUNT_BTC,
            "reason": reason,
            "action": action
        })
        if BTC_SCALP_SEND_TELEGRAM:
            send_telegram(btc_scalp_open_message(price, reason, st), route="scalp")
        return {"event": "scalp_buy", "position": "LONG"}

    if action in ["BTC_EXIT", "BTC_SCALP_EXIT"]:
        if st.get("position") != "LONG":
            btc_scalp_save_state(st)
            return {"event": "scalp_exit_ignored_flat", "position": "FLAT"}
        st, msg = btc_scalp_close(st, price, reason, "SELL")
        if msg and BTC_SCALP_SEND_TELEGRAM:
            send_telegram(msg, route="scalp")
        return {"event": "scalp_sell", "position": st.get("position")}

    return None


def format_btc_scalp_daily_summary(date_str=None):
    start, end = day_bounds(date_str)
    events = btc_scalp_closed_events(start, end)
    s = btc_scalp_summarize(events)
    st = btc_scalp_load_state()
    title_date = local_dt(start).strftime("%d-%m-%Y")
    lines = [
        "BTC SCALP PAPER DAGOVERZICHT",
        "",
        f"Datum: {title_date}",
        f"Mode: PAPER ONLY - geen Kraken-orders",
        f"Paper BTC per trade: {BTC_SCALP_PAPER_AMOUNT_BTC:.8f}",
        f"Early lock: +{BTC_SCALP_EARLY_LOCK_TRIGGER_POINTS:.0f} -> floor +{BTC_SCALP_EARLY_LOCK_FLOOR_POINTS:.0f}",
        f"Trail trigger: +{BTC_SCALP_TRAIL_TRIGGER_POINTS:.0f} punten",
        f"Trail stap: {BTC_SCALP_TRAIL_STEP_POINTS:.0f} punten",
        "",
        f"Gesloten trades: {s['closed_trades']}",
        f"Winsttrades: {s['wins']}",
        f"Verliestrades: {s['losses']}",
        f"Winrate: {s['winrate']:.1f}%",
        f"Punten bruto: {pts(s['points'])}",
        f"Paper resultaat: {fmt_eur(s['eur'])}",
    ]
    if s["best"]:
        lines.append(f"Beste scalp: {pts(s['best'].get('points'))} punten")
    if s["worst"]:
        lines.append(f"Slechtste scalp: {pts(s['worst'].get('points'))} punten")
    lines += [
        "",
        "Open scalp positie:",
        f"Status: {st.get('position')}",
        f"Entry: {fmt(st.get('entry_price'), 1)}",
        f"Laatste actie: {clean(st.get('last_action'))}",
    ]
    return "\n".join(lines)



def format_btc_scalp_total_summary():
    events = btc_scalp_closed_events()
    s = btc_scalp_summarize(events)
    st = btc_scalp_load_state()
    lines = [
        "BTC SCALP PAPER TOTAALOVERZICHT",
        "",
        "Mode: PAPER ONLY - geen Kraken-orders",
        f"Paper BTC per trade: {BTC_SCALP_PAPER_AMOUNT_BTC:.8f}",
        f"Early lock: +{BTC_SCALP_EARLY_LOCK_TRIGGER_POINTS:.0f} -> floor +{BTC_SCALP_EARLY_LOCK_FLOOR_POINTS:.0f}",
        f"Trail trigger: +{BTC_SCALP_TRAIL_TRIGGER_POINTS:.0f} punten",
        f"Trail stap: {BTC_SCALP_TRAIL_STEP_POINTS:.0f} punten",
        "",
        f"Gesloten scalps totaal: {s['closed_trades']}",
        f"Wins: {s['wins']}",
        f"Losses: {s['losses']}",
        f"Flats: {s['flats']}",
        f"Winrate: {s['winrate']:.1f}%",
        f"Punten totaal: {pts(s['points'])}",
        f"Paper resultaat totaal: {fmt_eur(s['eur'])}",
    ]
    if s["best"]:
        lines.append(f"Beste scalp totaal: {pts(s['best'].get('points'))} punten")
    if s["worst"]:
        lines.append(f"Slechtste scalp totaal: {pts(s['worst'].get('points'))} punten")
    lines += [
        "",
        "Open scalp positie:",
        f"Status: {st.get('position')}",
        f"Entry: {fmt(st.get('entry_price'), 1)}",
        f"Laatste actie: {clean(st.get('last_action'))}",
    ]
    return "\n".join(lines)

def closed_trade_events(start_ts=None, end_ts=None):
    events = []
    for e in load_trades():
        if e.get("type") != "CLOSED_TRADE":
            continue
        ts = int(e.get("ts") or 0)
        if start_ts is not None and ts < start_ts:
            continue
        if end_ts is not None and ts >= end_ts:
            continue
        events.append(e)
    return events


def summarize_closed_trades(events):
    total = len(events)
    wins = sum(1 for e in events if clean(e.get("result")).upper() == "WIN")
    losses = sum(1 for e in events if clean(e.get("result")).upper() == "LOSS")
    flats = total - wins - losses
    points = sum(fval(e.get("points"), 0.0) for e in events)
    gross_eur = sum(fval(e.get("gross_eur"), 0.0) for e in events)
    net_points_est = sum(fval(e.get("net_points_est"), fval(e.get("points"), 0.0)) for e in events)
    net_eur_est = sum(fval(e.get("net_eur_est"), fval(e.get("gross_eur"), 0.0)) for e in events)
    best = max(events, key=lambda e: fval(e.get("points"), 0.0), default=None)
    worst = min(events, key=lambda e: fval(e.get("points"), 0.0), default=None)
    winrate = (wins / total * 100.0) if total else 0.0
    return {
        "closed_trades": total,
        "wins": wins,
        "losses": losses,
        "flats": flats,
        "winrate": winrate,
        "points": points,
        "gross_eur": gross_eur,
        "net_points_est": net_points_est,
        "net_eur_est": net_eur_est,
        "best": best,
        "worst": worst
    }



def consecutive_losses(events):
    count = 0
    for e in reversed(events):
        result = clean(e.get("result")).upper()
        pts_value = fval(e.get("points"), 0.0)
        if result == "LOSS" or pts_value < 0:
            count += 1
            continue
        break
    return count


def timeframe_minutes_from_data(data):
    raw = clean(data.get("timeframe")) or clean(data.get("tf"))
    if raw:
        digits = "".join(ch for ch in raw if ch.isdigit())
        if digits:
            try:
                x = int(digits)
                if x > 0:
                    return x
            except Exception:
                pass
    return LOSS_GUARD_TIMEFRAME_MIN


def is_quality_override_reason(reason):
    if not LOSS_GUARD_ALLOW_QUALITY_OVERRIDE:
        return False
    r = clean(reason).upper()
    if not r:
        return False
    return any(k in r for k in QUALITY_OVERRIDE_KEYWORDS)


def loss_guard_active(state=None):
    if not LOSS_GUARD_ENABLED:
        return False
    if state is None:
        state = load_state()
    until_ts = int(fval(state.get("loss_guard_until_ts"), 0))
    return until_ts > now_ts()


def loss_guard_status_text(state=None):
    if state is None:
        state = load_state()
    until_ts = int(fval(state.get("loss_guard_until_ts"), 0))
    if until_ts <= now_ts():
        return "niet actief"
    mins_left = max(0, int((until_ts - now_ts() + 59) / 60))
    return f"actief tot {local_time_str(until_ts)} (nog ongeveer {mins_left} min)"


def maybe_arm_loss_guard_after_sell(data=None):
    if not LOSS_GUARD_ENABLED:
        return None

    day_start, day_end = day_bounds()
    events = closed_trade_events(day_start, day_end)
    day_sum = summarize_closed_trades(events)
    loss_streak = consecutive_losses(events)

    triggers = []
    if LOSS_STREAK_LIMIT > 0 and loss_streak >= LOSS_STREAK_LIMIT:
        triggers.append(f"{loss_streak} verliestrades achter elkaar")
    if day_sum["gross_eur"] <= LOSS_GUARD_DAILY_LIMIT_EUR:
        triggers.append(f"dagverlies {fmt_eur(day_sum['gross_eur'])} <= {fmt_eur(LOSS_GUARD_DAILY_LIMIT_EUR)}")

    if not triggers:
        return None

    tf_min = timeframe_minutes_from_data(data or {})
    cooldown_seconds = max(1, LOSS_GUARD_COOLDOWN_CANDLES) * max(1, tf_min) * 60
    until_ts = now_ts() + cooldown_seconds

    s = load_state()
    current_until = int(fval(s.get("loss_guard_until_ts"), 0))
    if until_ts <= current_until:
        return {
            "armed": False,
            "reason": s.get("loss_guard_reason", ""),
            "until_ts": current_until,
            "loss_streak": loss_streak,
            "day_gross_eur": day_sum["gross_eur"]
        }

    reason = "; ".join(triggers)
    s["loss_guard_until_ts"] = until_ts
    s["loss_guard_reason"] = reason
    s["loss_guard_last_arm_ts"] = now_ts()
    save_state(s)

    return {
        "armed": True,
        "reason": reason,
        "until_ts": until_ts,
        "loss_streak": loss_streak,
        "day_gross_eur": day_sum["gross_eur"]
    }


def buy_blocked_by_loss_guard(data, state=None):
    if not LOSS_GUARD_ENABLED:
        return (False, "")
    if state is None:
        state = load_state()
    if not loss_guard_active(state):
        return (False, "")

    reason = clean(data.get("reason")) or clean(data.get("exit_reason"))
    if is_quality_override_reason(reason):
        state["loss_guard_last_override_ts"] = now_ts()
        save_state(state)
        return (False, f"Quality override toegestaan tijdens chopbescherming: {reason}")

    state["loss_guard_last_block_ts"] = now_ts()
    save_state(state)
    until_ts = int(fval(state.get("loss_guard_until_ts"), 0))
    return (True, f"Render chopbescherming actief na {state.get('loss_guard_reason')}. Normale BUY geblokkeerd tot {local_time_str(until_ts)}. Rocket/breakout/HH-HL/reclaim override blijft toegestaan.")


def loss_guard_buy_log_text(data, state=None):
    if not LOSS_GUARD_ENABLED:
        return "Chopguard: uit"
    if state is None:
        state = load_state()

    reason = clean(data.get("reason")) or clean(data.get("exit_reason"))
    until_ts = int(fval(state.get("loss_guard_until_ts"), 0))
    active = loss_guard_active(state)
    override = bool(active and is_quality_override_reason(reason))

    if active:
        status = f"actief tot {local_time_str(until_ts)}"
    elif until_ts > 0:
        status = "niet actief / cooldown verlopen"
    else:
        status = "niet actief"

    lines = [
        "Chopguard:",
        f"- status: {status}",
        f"- reden guard: {clean(state.get('loss_guard_reason')) or '-'}",
        f"- buy reden: {reason or '-'}",
        f"- override toegestaan: {'ja' if override else 'nee'}"
    ]
    if active and override:
        lines.append("- actie: BUY mocht door via quality override")
    elif active:
        lines.append("- actie: normale BUY zou geblokkeerd worden")
    else:
        lines.append("- actie: BUY mocht door omdat guard niet actief was")
    return "\n".join(lines)

def day_bounds(date_str=None):
    if date_str:
        y, m, d = [int(x) for x in date_str.split("-")]
        tz = ZoneInfo(APP_TZ) if ZoneInfo else timezone.utc
        start_dt = datetime(y, m, d, 0, 0, 0, tzinfo=tz)
    else:
        now = local_dt()
        start_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end_dt = start_dt + timedelta(days=1)
    return int(start_dt.timestamp()), int(end_dt.timestamp())


def week_bounds(date_str=None):
    if date_str:
        y, m, d = [int(x) for x in date_str.split("-")]
        tz = ZoneInfo(APP_TZ) if ZoneInfo else timezone.utc
        dt = datetime(y, m, d, 12, 0, 0, tzinfo=tz)
    else:
        dt = local_dt()
    start_dt = (dt - timedelta(days=dt.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    end_dt = start_dt + timedelta(days=7)
    return int(start_dt.timestamp()), int(end_dt.timestamp())


def format_daily_summary(date_str=None):
    start, end = day_bounds(date_str)
    events = closed_trade_events(start, end)
    s = summarize_closed_trades(events)
    state = load_state()
    title_date = local_dt(start).strftime("%d-%m-%Y")

    lines = [
        "RBT DAGOVERZICHT",
        "",
        f"Datum: {title_date}",
        f"Gesloten trades: {s['closed_trades']}",
        f"Winsttrades: {s['wins']}",
        f"Verliestrades: {s['losses']}",
        f"Winrate: {s['winrate']:.1f}%",
        f"Punten bruto: {pts(s['points'])}",
        f"Bruto resultaat: {fmt_eur(s['gross_eur'])}",
        f"Geschat netto: {fmt_eur(s['net_eur_est'])}",
    ]

    if s["best"]:
        lines.append(f"Beste trade: {pts(s['best'].get('points'))} punten")
    if s["worst"]:
        lines.append(f"Slechtste trade: {pts(s['worst'].get('points'))} punten")

    lines += [
        "",
        "Open positie:",
        f"BTC: {fval(state.get('bot_position_btc'), 0.0):.8f}",
        f"Gemiddelde instap: {fmt(state.get('avg_entry_price'), 1)}",
        f"Laatste actie: {clean(state.get('last_action'))}",
    ]

    return "\n".join(lines)


def format_weekly_summary(date_str=None):
    start, end = week_bounds(date_str)
    events = closed_trade_events(start, end)
    s = summarize_closed_trades(events)
    state = load_state()

    day_lines = []
    for i in range(7):
        d_start = start + i * 86400
        d_end = d_start + 86400
        d_events = closed_trade_events(d_start, d_end)
        ds = summarize_closed_trades(d_events)
        day_name = local_dt(d_start).strftime("%a %d-%m")
        day_lines.append(f"{day_name}: {pts(ds['points'])} punten ({ds['closed_trades']} trades)")

    lines = [
        "RBT WEEKOVERZICHT",
        "",
        f"Week vanaf: {local_dt(start).strftime('%d-%m-%Y')}",
        "",
        *day_lines,
        "",
        f"Week totaal punten: {pts(s['points'])}",
        f"Bruto resultaat: {fmt_eur(s['gross_eur'])}",
        f"Geschat netto: {fmt_eur(s['net_eur_est'])}",
        f"Gesloten trades: {s['closed_trades']}",
        f"Winsttrades: {s['wins']}",
        f"Verliestrades: {s['losses']}",
        f"Winrate: {s['winrate']:.1f}%",
    ]

    if s["best"]:
        lines.append(f"Beste trade: {pts(s['best'].get('points'))} punten")
    if s["worst"]:
        lines.append(f"Slechtste trade: {pts(s['worst'].get('points'))} punten")

    lines += [
        "",
        "Open positie:",
        f"BTC: {fval(state.get('bot_position_btc'), 0.0):.8f}",
        f"Gemiddelde instap: {fmt(state.get('avg_entry_price'), 1)}",
        f"Laatste actie: {clean(state.get('last_action'))}",
    ]

    return "\n".join(lines)



# -----------------------------
# TURBOBOT PAPER ENGINE
# -----------------------------

def tb_roll_day_if_needed(state):
    today = local_date_str()
    if state.get("daily_date") != today:
        state["daily_date"] = today
        state["daily_realized_eur"] = 0.0
        state["daily_realized_pct"] = 0.0
        state["daily_closed_trades"] = 0
        state["daily_wins"] = 0
        state["daily_losses"] = 0
        state["daily_flats"] = 0
        state["daily_longs"] = 0
        state["daily_shorts"] = 0
        state["daily_locks"] = 0
        state["daily_target_hit"] = False
        state["daily_stop_hit"] = False
        state["cooldown_until_ts"] = 0
    return state


def tb_load_state():
    with TURBOBOT_LOCK:
        try:
            if os.path.exists(TURBOBOT_STATE_FILE):
                with open(TURBOBOT_STATE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                s = DEFAULT_TURBOBOT_STATE.copy()
                s.update(data)
                tb_roll_day_if_needed(s)
                return s
        except Exception as e:
            print("Turbobot state load error:", e)
        s = DEFAULT_TURBOBOT_STATE.copy()
        tb_roll_day_if_needed(s)
        return s


def tb_save_state(state):
    with TURBOBOT_LOCK:
        ensure_parent(TURBOBOT_STATE_FILE)
        state["last_update_ts"] = now_ts()
        with open(TURBOBOT_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)


def tb_reset_state():
    s = DEFAULT_TURBOBOT_STATE.copy()
    s["server_started_ts"] = now_ts()
    s["daily_date"] = local_date_str()
    tb_save_state(s)
    return s


def tb_load_events():
    with TURBOBOT_LOG_LOCK:
        try:
            if os.path.exists(TURBOBOT_LOG_FILE):
                with open(TURBOBOT_LOG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    return data
        except Exception as e:
            print("Turbobot log load error:", e)
        return []


def tb_save_events(events):
    with TURBOBOT_LOG_LOCK:
        ensure_parent(TURBOBOT_LOG_FILE)
        with open(TURBOBOT_LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(events, f, indent=2)


def tb_append_event(event):
    events = tb_load_events()
    event = dict(event)
    event.setdefault("ts", now_ts())
    event.setdefault("tijd", local_time_str(event.get("ts")))
    event.setdefault("datum", local_date_str(event.get("ts")))
    events.append(event)
    tb_save_events(events)
    return event


def tb_signal_from_data(data):
    explicit = clean(data.get("signal") or data.get("action") or data.get("trade_action") or data.get("pine_action") or data.get("order_action"))
    raw = " ".join([
        clean(data.get("signal")),
        clean(data.get("action")),
        clean(data.get("trade_action")),
        clean(data.get("pine_action")),
        clean(data.get("order_action")),
        clean(data.get("side")),
        clean(data.get("type")),
        clean(data.get("event")),
        clean(data.get("alert")),
        clean(data.get("message")),
        clean(data.get("title")),
        clean(data.get("reason")),
        clean(data.get("exit_reason")),
    ]).upper().replace("/", "_").replace("-", "_")

    if "LOCK_SHORT" in raw or "LOCK SHORT" in raw:
        return "LOCK_SHORT"
    if "LOCK_LONG" in raw or "LOCK LONG" in raw:
        return "LOCK_LONG"
    if "SELL_LONG" in raw or "SELL LONG" in raw or "LONG OUT" in raw or "LONG_EXIT" in raw or "EXIT_LONG" in raw:
        return "SELL_LONG"
    if "BUY_SHORT" in raw or "BUY SHORT" in raw or "SHORT OUT" in raw or "SHORT_EXIT" in raw or "EXIT_SHORT" in raw:
        return "BUY_SHORT"
    if "SHORT" in raw and "LOCK" not in raw and "BUY" not in raw:
        return "SHORT"
    if "LONG" in raw and "LOCK" not in raw and "SELL" not in raw:
        return "LONG"

    # FIX V9.22: fallback voor oude/afwijkende Turbobot alerts waarin action leeg blijft,
    # maar Pine wel trade_buy/trade_sell of entry/exit velden meestuurt.
    # Dit raakt BTC/Kraken niet; het wordt alleen gebruikt binnen handle_turbobot_alert().
    if is_turbobot_alert(data):
        if any(clean(data.get(k)) for k in ["trade_sell", "trade_exit_price", "exit_price", "sell_price", "pine_sell"]):
            return "SELL_LONG"
        if any(clean(data.get(k)) for k in ["trade_buy", "trade_entry_price", "entry_price", "buy_price", "pine_buy"]):
            return "LONG"

    return explicit.upper()


def is_turbobot_alert(data):
    blob = " ".join([
        clean(data.get("bot")),
        clean(data.get("version")),
        clean(data.get("strategy")),
        clean(data.get("mode")),
        clean(data.get("source")),
        clean(data.get("message")),
        clean(data.get("title")),
        clean(data.get("signal")),
        clean(data.get("action")),
    ]).upper()
    symbol = clean(data.get("ticker") or data.get("symbol") or data.get("market")).upper()
    sig = clean(data.get("signal") or data.get("action") or data.get("trade_action") or data.get("pine_action")).upper()

    if "TURBOBOT" in blob or clean(data.get("bot")).upper().startswith("TB"):
        return True
    if clean(data.get("version")).upper() in ["10B", "TB10B", "TURBOBOT 10B"]:
        return True
    if symbol in ["NBIS", "NASDAQ:NBIS"] and sig in ["LONG", "SHORT", "SELL_LONG", "BUY_SHORT", "LOCK_LONG", "LOCK_SHORT"]:
        return True
    return False


def tb_price_from_data(data):
    return fval(data.get("price") or data.get("close") or data.get("last") or data.get("mark"), None)


def tb_symbol_from_data(data):
    return clean(data.get("ticker") or data.get("symbol") or data.get("market"))


def tb_timeframe_from_data(data):
    return clean(data.get("timeframe") or data.get("tf") or data.get("interval"))


def tb_reason_from_data(data):
    return clean(data.get("reason") or data.get("exit_reason") or data.get("phase") or data.get("regime") or data.get("comment"))


def tb_open_pnl(state, current_price):
    pos = clean(state.get("position")).upper()
    entry = fval(state.get("entry_price"), None)
    price = fval(current_price, None)
    trade_size = fval(state.get("trade_size_eur"), TURBOBOT_START_CAPITAL * TURBOBOT_TRADE_FRACTION)
    lev = fval(state.get("leverage"), TURBOBOT_LEVERAGE)
    if pos not in ["LONG", "SHORT"] or entry is None or price is None or entry <= 0:
        return 0.0, 0.0
    if pos == "LONG":
        pct = ((price - entry) / entry) * lev * 100.0
    else:
        pct = ((entry - price) / entry) * lev * 100.0
    eur = trade_size * pct / 100.0
    return eur, pct


def tb_can_open_new_trade(state):
    tb_roll_day_if_needed(state)
    if bval(state.get("kill_switch")):
        return False, "Kill switch staat aan."
    if bval(state.get("daily_stop_hit")):
        return False, "Dagstop geraakt. Geen nieuwe Turbobot paper-trades."
    if bval(state.get("daily_target_hit")) and TURBOBOT_DAILY_TARGET_BLOCKS_NEW_TRADES:
        return False, "Dagtarget geraakt. Nieuwe trades worden geblokkeerd."
    if int(state.get("daily_closed_trades") or 0) >= TURBOBOT_MAX_TRADES_PER_DAY:
        return False, f"Max trades per dag bereikt ({TURBOBOT_MAX_TRADES_PER_DAY})."
    if now_ts() < int(state.get("cooldown_until_ts") or 0):
        return False, "Cooldown na verlies is nog actief."
    return True, "OK"


def tb_open_position(state, side, price, data, note=""):
    state["position"] = side
    state["symbol"] = tb_symbol_from_data(data)
    state["timeframe"] = tb_timeframe_from_data(data)
    state["entry_price"] = price
    state["entry_ts"] = now_ts()
    state["entry_signal"] = side
    state["entry_reason"] = tb_reason_from_data(data) or note
    state["trade_size_eur"] = fval(state.get("capital"), TURBOBOT_START_CAPITAL) * TURBOBOT_TRADE_FRACTION
    state["leverage"] = TURBOBOT_LEVERAGE
    if side == "LONG":
        state["daily_longs"] = int(state.get("daily_longs") or 0) + 1
    if side == "SHORT":
        state["daily_shorts"] = int(state.get("daily_shorts") or 0) + 1
    tb_append_event({
        "type": "OPEN",
        "side": side,
        "symbol": state.get("symbol"),
        "timeframe": state.get("timeframe"),
        "price": price,
        "trade_size_eur": state.get("trade_size_eur"),
        "leverage": state.get("leverage"),
        "reason": state.get("entry_reason"),
        "raw": data
    })
    return state


def tb_close_position(state, price, data, reason=""):
    side = clean(state.get("position")).upper()
    if side not in ["LONG", "SHORT"]:
        return state, None

    entry = fval(state.get("entry_price"), None)
    pnl_eur, pnl_pct = tb_open_pnl(state, price)
    result = result_from_eur(pnl_eur)
    state["capital"] = fval(state.get("capital"), TURBOBOT_START_CAPITAL) + pnl_eur
    state["last_closed_pnl_eur"] = pnl_eur
    state["last_closed_pnl_pct"] = pnl_pct
    state["last_closed_result"] = result
    state["last_closed_side"] = side
    state["last_closed_reason"] = reason or tb_reason_from_data(data)
    state["daily_realized_eur"] = fval(state.get("daily_realized_eur"), 0.0) + pnl_eur
    state["daily_realized_pct"] = (fval(state.get("daily_realized_eur"), 0.0) / TURBOBOT_START_CAPITAL) * 100.0
    state["daily_closed_trades"] = int(state.get("daily_closed_trades") or 0) + 1
    if result == "WIN":
        state["daily_wins"] = int(state.get("daily_wins") or 0) + 1
    elif result == "LOSS":
        state["daily_losses"] = int(state.get("daily_losses") or 0) + 1
        state["cooldown_until_ts"] = now_ts() + TURBOBOT_COOLDOWN_AFTER_LOSS_SEC
    else:
        state["daily_flats"] = int(state.get("daily_flats") or 0) + 1

    state["daily_target_hit"] = fval(state.get("daily_realized_pct"), 0.0) >= TURBOBOT_DAILY_TARGET_PCT
    state["daily_stop_hit"] = fval(state.get("daily_realized_pct"), 0.0) <= TURBOBOT_DAILY_STOP_PCT

    closed = {
        "type": "CLOSED_TRADE",
        "side": side,
        "symbol": state.get("symbol"),
        "timeframe": state.get("timeframe"),
        "entry_price": entry,
        "exit_price": price,
        "pnl_eur": pnl_eur,
        "pnl_pct": pnl_pct,
        "result": result,
        "trade_size_eur": state.get("trade_size_eur"),
        "leverage": state.get("leverage"),
        "reason": state.get("last_closed_reason"),
        "capital_after": state.get("capital"),
        "daily_realized_eur": state.get("daily_realized_eur"),
        "daily_realized_pct": state.get("daily_realized_pct"),
        "raw": data
    }
    tb_append_event(closed)

    state["position"] = "FLAT"
    state["entry_price"] = None
    state["entry_ts"] = None
    state["entry_signal"] = ""
    state["entry_reason"] = ""
    state["trade_size_eur"] = None
    return state, closed


def tb_status_lines(state, price=None):
    open_eur, open_pct = tb_open_pnl(state, price if price is not None else state.get("last_price"))
    pos = clean(state.get("position")).upper() or "FLAT"
    return [
        f"Positie: {pos}",
        f"Kapitaal: EUR {fval(state.get('capital'), TURBOBOT_START_CAPITAL):.2f}",
        f"Open P/L: {fmt_eur(open_eur)} ({fmt_pct(open_pct)})",
        f"Dag P/L: {fmt_eur(state.get('daily_realized_eur'))} ({fmt_pct(state.get('daily_realized_pct'))})",
        f"Dagtarget {TURBOBOT_DAILY_TARGET_PCT:.1f}%: {'JA' if bval(state.get('daily_target_hit')) else 'nee'}",
        f"Runner mode: {'AAN' if (bval(state.get('daily_target_hit')) and TURBOBOT_RUNNER_MODE_AFTER_TARGET and not TURBOBOT_DAILY_TARGET_BLOCKS_NEW_TRADES) else 'nee'}",
        f"Dagstop {TURBOBOT_DAILY_STOP_PCT:.1f}%: {'JA' if bval(state.get('daily_stop_hit')) else 'nee'}",
        f"Trades vandaag: {int(state.get('daily_closed_trades') or 0)} / {TURBOBOT_MAX_TRADES_PER_DAY}",
    ]


def tb_format_message(kind, signal, state, price, data, extra_lines=None):
    symbol = tb_symbol_from_data(data) or clean(state.get("symbol"))
    tf = tb_timeframe_from_data(data) or clean(state.get("timeframe"))
    reason = tb_reason_from_data(data)
    pos = clean(state.get("position")).upper() or "FLAT"
    open_eur, open_pct = tb_open_pnl(state, price)

    lines = [
        f"TURBOBOT {signal}",
        f"Ticker: {symbol} {tf}",
        f"Prijs: {fmt(price, 3)}",
        f"Positie: {pos}",
        f"Paper: EUR {TURBOBOT_START_CAPITAL:.0f} | inzet {TURBOBOT_TRADE_FRACTION * 100:.0f}% | hefboom {TURBOBOT_LEVERAGE:.1f}x",
        f"Dag P/L: {fmt_eur(fval(state.get('daily_realized_eur'), 0.0))} ({fval(state.get('daily_realized_pct'), 0.0):+.2f}%)",
        f"Runner mode: {'AAN' if (bval(state.get('daily_target_hit')) and TURBOBOT_RUNNER_MODE_AFTER_TARGET and not TURBOBOT_DAILY_TARGET_BLOCKS_NEW_TRADES) else 'nee'}",
        f"Trades: {int(state.get('daily_closed_trades') or 0)} | W/L/F {int(state.get('daily_wins') or 0)}/{int(state.get('daily_losses') or 0)}/{int(state.get('daily_flats') or 0)}",
    ]
    if pos in ["LONG", "SHORT"]:
        lines.append(f"Open P/L: {fmt_eur(open_eur)} ({open_pct:+.2f}%)")
    if reason:
        lines.append(f"Reden: {reason}")
    if extra_lines:
        # Houd alleen de nuttige extra regels; geen lange debugdump in normale Telegram.
        lines += [line for line in extra_lines if clean(line)]
    lines.append(f"Tijd: {local_time_str()}")
    return "\n".join(lines)


def handle_turbobot_alert(data):
    state = tb_load_state()
    tb_roll_day_if_needed(state)
    signal = tb_signal_from_data(data)
    price = tb_price_from_data(data)
    if price is None or price <= 0:
        msg = tb_format_message("BLOCK", "GEEN GELDIGE PRIJS", state, 0, data, ["Alert genegeerd: price/close ontbreekt."])
        send_telegram(msg)
        return {"ok": False, "reason": "missing_price", "signal": signal, "state": state}

    state["last_signal"] = signal
    state["last_price"] = price
    pos = clean(state.get("position")).upper() or "FLAT"

    # V9.27: na dagstop geen Telegram-spam meer voor nieuwe entries/locks/loze exits.
    # Dit verandert niets aan Pine of dagrapport; het houdt alleen Render schoon.
    if bval(state.get("daily_stop_hit")) and TURBOBOT_SILENCE_AFTER_DAILY_STOP:
        if pos == "FLAT" and signal in ["LONG", "SHORT", "LOCK_LONG", "LOCK_SHORT", "SELL_LONG", "BUY_SHORT"]:
            tb_append_event({"type": "SILENT_AFTER_DAYSTOP", "signal": signal, "price": price, "position": pos, "raw": data})
            tb_save_state(state)
            return {"ok": True, "signal": signal, "silent": True, "reason": "daily_stop_flat", "state": state}

    response_kind = "INFO"
    response_signal = signal
    extra = []

    # V9.27: LOCK is alleen winstbescherming/handrem. Geen lock bij verlies en geen onbekend-signaal spam.
    if signal == "LOCK_LONG":
        if pos == "LONG":
            open_eur, open_pct = tb_open_pnl(state, price)
            if (not TURBOBOT_LOCK_REQUIRES_PROFIT) or open_pct > TURBOBOT_LOCK_MIN_OPEN_PCT:
                state["daily_locks"] = int(state.get("daily_locks") or 0) + 1
                tb_append_event({"type": "LOCK", "side": "LONG", "price": price, "open_pnl_eur": open_eur, "open_pnl_pct": open_pct, "raw": data})
                response_kind = "LOCK"
                extra = ["LOCK_LONG bevestigd: winst beschermen / trailing aanscherpen. Geen paper-close."]
            else:
                tb_append_event({"type": "LOCK_IGNORED", "side": "LONG", "price": price, "open_pnl_eur": open_eur, "open_pnl_pct": open_pct, "reason": "no_profit", "raw": data})
                tb_save_state(state)
                if TURBOBOT_SILENCE_BAD_LOCKS:
                    return {"ok": True, "signal": signal, "silent": True, "reason": "lock_without_profit", "state": state}
                response_kind = "INFO"
                extra = [f"LOCK_LONG genegeerd: open P/L is {fmt_pct(open_pct)}."]
        else:
            tb_append_event({"type": "LOCK_IGNORED", "side": "LONG", "price": price, "position": pos, "reason": "wrong_position", "raw": data})
            tb_save_state(state)
            if TURBOBOT_SILENCE_BAD_LOCKS:
                return {"ok": True, "signal": signal, "silent": True, "reason": "lock_wrong_position", "state": state}
            response_kind = "INFO"
            extra = [f"LOCK_LONG genegeerd: huidige positie is {pos}."]
    elif signal == "LOCK_SHORT":
        if pos == "SHORT":
            open_eur, open_pct = tb_open_pnl(state, price)
            if (not TURBOBOT_LOCK_REQUIRES_PROFIT) or open_pct > TURBOBOT_LOCK_MIN_OPEN_PCT:
                state["daily_locks"] = int(state.get("daily_locks") or 0) + 1
                tb_append_event({"type": "LOCK", "side": "SHORT", "price": price, "open_pnl_eur": open_eur, "open_pnl_pct": open_pct, "raw": data})
                response_kind = "LOCK"
                extra = ["LOCK_SHORT bevestigd: winst beschermen / trailing aanscherpen. Geen paper-close."]
            else:
                tb_append_event({"type": "LOCK_IGNORED", "side": "SHORT", "price": price, "open_pnl_eur": open_eur, "open_pnl_pct": open_pct, "reason": "no_profit", "raw": data})
                tb_save_state(state)
                if TURBOBOT_SILENCE_BAD_LOCKS:
                    return {"ok": True, "signal": signal, "silent": True, "reason": "lock_without_profit", "state": state}
                response_kind = "INFO"
                extra = [f"LOCK_SHORT genegeerd: open P/L is {fmt_pct(open_pct)}."]
        else:
            tb_append_event({"type": "LOCK_IGNORED", "side": "SHORT", "price": price, "position": pos, "reason": "wrong_position", "raw": data})
            tb_save_state(state)
            if TURBOBOT_SILENCE_BAD_LOCKS:
                return {"ok": True, "signal": signal, "silent": True, "reason": "lock_wrong_position", "state": state}
            response_kind = "INFO"
            extra = [f"LOCK_SHORT genegeerd: huidige positie is {pos}."]
    elif signal == "LONG":
        if pos == "LONG":
            response_kind = "INFO"
            extra = ["LONG genegeerd: Turbobot staat al LONG."]
        elif pos == "SHORT":
            state, closed = tb_close_position(state, price, data, "FLIP SHORT -> LONG")
            can_open, why = tb_can_open_new_trade(state)
            if can_open:
                state = tb_open_position(state, "LONG", price, data, "FLIP SHORT -> LONG")
                response_kind = "FLIP"
                response_signal = "SHORT -> LONG"
                extra = [f"SHORT gesloten: {fmt_eur(closed.get('pnl_eur'))} ({fmt_pct(closed.get('pnl_pct'))})", "Nieuwe paper LONG geopend."]
            else:
                response_kind = "CLOSE"
                response_signal = "SHORT GESLOTEN"
                extra = [f"SHORT gesloten: {fmt_eur(closed.get('pnl_eur'))} ({fmt_pct(closed.get('pnl_pct'))})", f"Nieuwe LONG geblokkeerd: {why}"]
        else:
            can_open, why = tb_can_open_new_trade(state)
            if can_open:
                state = tb_open_position(state, "LONG", price, data, "LONG")
                response_kind = "LONG"
                extra = ["Nieuwe paper LONG geopend."]
            else:
                tb_append_event({"type": "OPEN_BLOCKED", "side": "LONG", "price": price, "reason": why, "raw": data})
                tb_save_state(state)
                if bval(state.get("daily_stop_hit")) and TURBOBOT_SILENCE_AFTER_DAILY_STOP:
                    return {"ok": True, "signal": signal, "silent": True, "reason": "daily_stop", "state": state}
                response_kind = "BLOCK"
                extra = [f"LONG geblokkeerd: {why}"]
    elif signal == "SHORT":
        if pos == "SHORT":
            response_kind = "INFO"
            extra = ["SHORT genegeerd: Turbobot staat al SHORT."]
        elif pos == "LONG":
            state, closed = tb_close_position(state, price, data, "FLIP LONG -> SHORT")
            can_open, why = tb_can_open_new_trade(state)
            if can_open:
                state = tb_open_position(state, "SHORT", price, data, "FLIP LONG -> SHORT")
                response_kind = "FLIP"
                response_signal = "LONG -> SHORT"
                extra = [f"LONG gesloten: {fmt_eur(closed.get('pnl_eur'))} ({fmt_pct(closed.get('pnl_pct'))})", "Nieuwe paper SHORT geopend."]
            else:
                response_kind = "CLOSE"
                response_signal = "LONG GESLOTEN"
                extra = [f"LONG gesloten: {fmt_eur(closed.get('pnl_eur'))} ({fmt_pct(closed.get('pnl_pct'))})", f"Nieuwe SHORT geblokkeerd: {why}"]
        else:
            can_open, why = tb_can_open_new_trade(state)
            if can_open:
                state = tb_open_position(state, "SHORT", price, data, "SHORT")
                response_kind = "SHORT"
                extra = ["Nieuwe paper SHORT geopend."]
            else:
                tb_append_event({"type": "OPEN_BLOCKED", "side": "SHORT", "price": price, "reason": why, "raw": data})
                tb_save_state(state)
                if bval(state.get("daily_stop_hit")) and TURBOBOT_SILENCE_AFTER_DAILY_STOP:
                    return {"ok": True, "signal": signal, "silent": True, "reason": "daily_stop", "state": state}
                response_kind = "BLOCK"
                extra = [f"SHORT geblokkeerd: {why}"]
    elif signal == "SELL_LONG":
        if pos == "LONG":
            state, closed = tb_close_position(state, price, data, "SELL_LONG")
            response_kind = "CLOSE"
            response_signal = "SELL LONG"
            extra = [f"LONG gesloten: {fmt_eur(closed.get('pnl_eur'))} ({fmt_pct(closed.get('pnl_pct'))})"]
        else:
            tb_append_event({"type": "EXIT_IGNORED", "signal": signal, "price": price, "position": pos, "raw": data})
            tb_save_state(state)
            if TURBOBOT_SILENCE_IGNORED_FLAT_EXITS:
                return {"ok": True, "signal": signal, "silent": True, "reason": "flat_exit_ignored", "state": state}
            response_kind = "INFO"
            extra = [f"SELL_LONG genegeerd: huidige positie is {pos}."]
    elif signal == "BUY_SHORT":
        if pos == "SHORT":
            state, closed = tb_close_position(state, price, data, "BUY_SHORT")
            response_kind = "CLOSE"
            response_signal = "BUY SHORT"
            extra = [f"SHORT gesloten: {fmt_eur(closed.get('pnl_eur'))} ({fmt_pct(closed.get('pnl_pct'))})"]
        else:
            tb_append_event({"type": "EXIT_IGNORED", "signal": signal, "price": price, "position": pos, "raw": data})
            tb_save_state(state)
            if TURBOBOT_SILENCE_IGNORED_FLAT_EXITS:
                return {"ok": True, "signal": signal, "silent": True, "reason": "flat_exit_ignored", "state": state}
            response_kind = "INFO"
            extra = [f"BUY_SHORT genegeerd: huidige positie is {pos}."]
    else:
        response_kind = "BLOCK"
        extra = [f"Onbekend Turbobot-signaal: {signal}"]

    tb_roll_day_if_needed(state)
    tb_save_state(state)
    msg = tb_format_message(response_kind, response_signal, state, price, data, extra)
    send_telegram(msg)
    return {"ok": True, "signal": signal, "state": state, "message": msg}

def tb_events_in_period(start_ts=None, end_ts=None):
    out = []
    for e in tb_load_events():
        ts = int(e.get("ts") or 0)
        if start_ts is not None and ts < start_ts:
            continue
        if end_ts is not None and ts >= end_ts:
            continue
        out.append(e)
    return out


def format_turbobot_daily_summary(date_str=None):
    start, end = day_bounds(date_str)
    events = tb_events_in_period(start, end)
    closed = [e for e in events if e.get("type") == "CLOSED_TRADE"]
    locks = [e for e in events if e.get("type") == "LOCK"]
    wins = sum(1 for e in closed if clean(e.get("result")).upper() == "WIN")
    losses = sum(1 for e in closed if clean(e.get("result")).upper() == "LOSS")
    flats = len(closed) - wins - losses
    pnl_eur = sum(fval(e.get("pnl_eur"), 0.0) for e in closed)
    pnl_pct = pnl_eur / TURBOBOT_START_CAPITAL * 100.0
    longs = sum(1 for e in closed if clean(e.get("side")).upper() == "LONG")
    shorts = sum(1 for e in closed if clean(e.get("side")).upper() == "SHORT")
    best = max(closed, key=lambda e: fval(e.get("pnl_eur"), 0.0), default=None)
    worst = min(closed, key=lambda e: fval(e.get("pnl_eur"), 0.0), default=None)
    winrate = wins / len(closed) * 100.0 if closed else 0.0
    state = tb_load_state()

    lines = [
        "TURBOBOT DAGREPORT",
        "",
        f"Datum: {local_dt(start).strftime('%d-%m-%Y')}",
        "Mode: PAPER / SIGNAL ONLY",
        f"Startkapitaal sim: EUR {TURBOBOT_START_CAPITAL:.2f}",
        f"Hefboom sim: {TURBOBOT_LEVERAGE:.1f}x",
        f"Inzet per trade: {TURBOBOT_TRADE_FRACTION * 100:.0f}%",
        "",
        f"Gesloten trades: {len(closed)}",
        f"Wins: {wins}",
        f"Losses: {losses}",
        f"Flats: {flats}",
        f"Winrate: {winrate:.1f}%",
        f"Long trades: {longs}",
        f"Short trades: {shorts}",
        f"Locks: {len(locks)}",
        "",
        f"Dag P/L: {fmt_eur(pnl_eur)} ({fmt_pct(pnl_pct)})",
        f"Dagtarget {TURBOBOT_DAILY_TARGET_PCT:.1f}%: {'JA' if pnl_pct >= TURBOBOT_DAILY_TARGET_PCT else 'nee'}",
        f"Runner mode na target: {'AAN' if (pnl_pct >= TURBOBOT_DAILY_TARGET_PCT and TURBOBOT_RUNNER_MODE_AFTER_TARGET and not TURBOBOT_DAILY_TARGET_BLOCKS_NEW_TRADES) else 'nee'}",
        f"Dagtarget blokkeert nieuwe trades: {'JA' if TURBOBOT_DAILY_TARGET_BLOCKS_NEW_TRADES else 'nee'}",
        f"Dagstop {TURBOBOT_DAILY_STOP_PCT:.1f}%: {'JA' if pnl_pct <= TURBOBOT_DAILY_STOP_PCT else 'nee'}",
    ]
    if best:
        lines.append(f"Beste trade: {fmt_eur(best.get('pnl_eur'))} ({fmt_pct(best.get('pnl_pct'))})")
    if worst:
        lines.append(f"Slechtste trade: {fmt_eur(worst.get('pnl_eur'))} ({fmt_pct(worst.get('pnl_pct'))})")
    lines += ["", "Open status:", *tb_status_lines(state, state.get("last_price"))]
    return "\n".join(lines)

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
    mode = clean(data.get("mode")).upper()
    trade_mode = clean(data.get("trade_mode")).upper()

    ticker_clean = ticker.replace("/", "").replace("-", "").replace(".", "")
    ticker_ok = ticker_clean in [
        "BTCEUR",
        "XBTEUR",
        "XBTEURP",
        "XBTEURPERP",
        "XXBTZEUR",
        "XXBTZEURP",
    ]

    action_ok = action in ["BTC_BUY", "BTC_EXIT"]

    text_blob = " ".join([bot, version, strategy, mode, trade_mode]).upper()

    rbt_or_rene = (
        "RBT" in text_blob
        or "RENE" in text_blob
        or "BTC SPOT BOT" in text_blob
        or "BTC TRENDBOT 1" in text_blob
        or "BTC_TREND_1" in text_blob
        or "BTC TREND" in text_blob
        or "TRENDBOT" in text_blob
    )

    kraken_context = (
        "KRAKEN" in text_blob
        or bval(data.get("kraken_order"))
        or trade_mode == "KRAKEN_LIVE"
        or mode == "KRAKEN_LIVE"
        or EXCHANGE_ENV.lower() == "kraken"
    )

    live_context = (
        bval(data.get("live"))
        or bval(data.get("is_live"))
        or bval(data.get("place_order"))
        or bval(data.get("execute"))
        or bval(data.get("live_order"))
        or trade_mode == "KRAKEN_LIVE"
        or mode == "KRAKEN_LIVE"
    )

    return ticker_ok and action_ok and rbt_or_rene and kraken_context and live_context


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
    # SELL: verkoop wat Pine vraagt/server toestaat. BUY: standaard gebruikt Render de afgesproken inzet,
    # zodat oude Pine JSON met 0.00010 niet per ongeluk de inzet klein houdt.
    if action == "BTC_EXIT":
        keys = ["sell_amount_btc", "max_sell_btc", "amount_btc", "volume", "qty", "quantity"]
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

    if not HONOR_TV_BUY_VOLUME:
        return f"{float(DEFAULT_BTC_VOLUME):.8f}"

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

    return f"{float(DEFAULT_BTC_VOLUME):.8f}"


def pine_trade_text(data, action, price):
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


def base_message(data):
    return f"""Trading Alert
Bot: {clean(data.get("bot"))}
Ticker: {clean(data.get("ticker"))}
Actie: {clean(data.get("action"))}
Prijs: {clean(data.get("price"))}
Timeframe: {clean(data.get("timeframe")) or clean(data.get("tf"))}
"""


def blocked_message(data, reason):
    return base_message(data) + pine_trade_text(data, clean(data.get("action")), clean(data.get("price"))) + f"""
LET OP - Kraken-order NIET uitgevoerd
Reden: {reason}
Tijd: {local_time_str()}
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
    s["last_buy_ts"] = now_ts()
    s["last_action"] = "BUY"
    s["open_trade_id"] = s.get("open_trade_id") or oid or f"buy-{now_ts()}"
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
    net_points_est = None
    net_eur_est = None
    trade_result = None
    warning = ""

    if entry_price is not None and sell_price is not None:
        trade_points = sell_price - entry_price
        gross_eur = trade_points * volume
        net_points_est = trade_points - ROUND_TRIP_COST_POINTS
        net_eur_est = net_points_est * volume
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
    s["last_sell_ts"] = now_ts()
    s["last_action"] = "SELL"
    s["last_trade_points"] = trade_points
    s["last_trade_gross_eur"] = gross_eur
    s["last_trade_net_points_est"] = net_points_est
    s["last_trade_net_eur_est"] = net_eur_est
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
        s["avg_entry_price"] = entry_price

    save_state(s)
    return s


def buy_message(bot, ticker, price, volume, oid, reason, state, chopguard_log=""):
    price_f = fval(price, None)
    volume_f = fval(volume, 0.0)
    order_value = price_f * volume_f if price_f is not None else None
    position_btc = fval(state.get("bot_position_btc"), 0.0)
    avg_entry = fval(state.get("avg_entry_price"), None)
    position_value = position_btc * avg_entry if avg_entry is not None else None

    return f"""KRAKEN BUY UITGEVOERD
Bot: {bot}
Ticker: {ticker}
Koers: {fmt(price_f, 1)}
Aantal BTC: {volume_f:.8f}
Waarde: {fmt_eur_abs(order_value, 2)}
Reden: {reason}

Botpositie: {position_btc:.8f} BTC
Gem. instap: {fmt(avg_entry, 1)}
Positiewaarde: {fmt_eur_abs(position_value, 2)}
Tijd: {local_time_str()}
{("\n" + chopguard_log) if chopguard_log else ""}
"""


def sell_message(bot, ticker, price, volume, oid, reason, state, entry_before, pine_entry):
    entry = fval(entry_before, None)
    exitp = fval(price, None)
    vol = fval(volume, 0.0)
    points = None
    gross_eur = None
    net_points = None
    net_eur = None
    result = "FLAT"

    if entry is not None and exitp is not None:
        points = exitp - entry
        gross_eur = points * vol
        net_points = points - ROUND_TRIP_COST_POINTS
        net_eur = net_points * vol
        result = result_from_points(points)

    day_start, day_end = day_bounds()
    day_sum = summarize_closed_trades(closed_trade_events(day_start, day_end))

    lines = [
        "KRAKEN SELL UITGEVOERD",
        f"Bot: {bot}",
        f"Ticker: {ticker}",
        f"Koers verkoop: {fmt(exitp, 1)}",
        f"Aantal BTC: {vol:.8f}",
        f"Verkoopwaarde: {fmt_eur_abs(exitp * vol if exitp is not None else None, 2)}",
        f"Reden: {reason}",
        "",
        f"Instap: {fmt(entry, 1)}",
        f"Exit: {fmt(exitp, 1)}",
        f"Punten: {pts(points)}",
        f"Resultaat bruto: {fmt_eur(gross_eur)}",
        f"Geschat netto: {fmt_eur(net_eur)}",
        f"Resultaat: {nl_result(result)}",
    ]

    pine = fval(pine_entry, None)
    if pine is not None and entry is not None:
        diff = pine - entry
        if abs(diff) >= 1.0:
            lines += [
                "",
                "Controle:",
                f"Pine instap: {fmt(pine, 1)}",
                f"Server instap: {fmt(entry, 1)}",
                f"Verschil: {pts(diff)} punten",
                "LET OP: server/Kraken-resultaat is leidend.",
            ]

    lines += [
        "",
        "Dag totaal:",
        f"Punten: {pts(day_sum['points'])}",
        f"Bruto EUR: {fmt_eur(day_sum['gross_eur'])}",
        f"Geschat netto: {fmt_eur(day_sum['net_eur_est'])}",
        f"Gesloten trades: {day_sum['closed_trades']}",
        "",
        f"Serverpositie: {fval(state.get('bot_position_btc'), 0.0):.8f} BTC",
        f"Tijd: {local_time_str()}",
    ]

    return "\n".join(lines)


@app.route("/")
def home():
    return jsonify({
        "status": "BTC Trendbot 1 LIVE + BTC Scalp Paper + Turbobot Paper Engine draait",
        "version": "app.py V9.34 COMBINED BTC TRENDBOT 1 + BTC SCALPBOT 1 PAPER TRAIL250",
        "pair": PAIR,
        "env_live_allowed": env_live_allowed(),
        "state_file": STATE_FILE,
        "trade_log_file": TRADE_LOG_FILE,
        "turbobot_state_file": TURBOBOT_STATE_FILE,
        "turbobot_log_file": TURBOBOT_LOG_FILE,
        "timezone": APP_TZ,
        "state": load_state(),
        "turbobot_state": tb_load_state()
    })


@app.route("/status")
def status():
    return jsonify({
        "version": "app.py V9.34 COMBINED BTC TRENDBOT 1 + BTC SCALPBOT 1 PAPER TRAIL250",
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
            "BOT_STATE_FILE": STATE_FILE,
            "TRADE_LOG_FILE": TRADE_LOG_FILE,
            "APP_TZ": APP_TZ,
            "ROUND_TRIP_COST_POINTS": ROUND_TRIP_COST_POINTS,
            "LOSS_GUARD_ENABLED": LOSS_GUARD_ENABLED,
            "LOSS_STREAK_LIMIT": LOSS_STREAK_LIMIT,
            "LOSS_GUARD_DAILY_LIMIT_EUR": LOSS_GUARD_DAILY_LIMIT_EUR,
            "LOSS_GUARD_COOLDOWN_CANDLES": LOSS_GUARD_COOLDOWN_CANDLES,
            "LOSS_GUARD_TIMEFRAME_MIN": LOSS_GUARD_TIMEFRAME_MIN,
            "LOSS_GUARD_ALLOW_QUALITY_OVERRIDE": LOSS_GUARD_ALLOW_QUALITY_OVERRIDE,
            "QUALITY_OVERRIDE_KEYWORDS": QUALITY_OVERRIDE_KEYWORDS,
            "TURBOBOT_STATE_FILE": TURBOBOT_STATE_FILE,
            "TURBOBOT_LOG_FILE": TURBOBOT_LOG_FILE,
            "TURBOBOT_START_CAPITAL": TURBOBOT_START_CAPITAL,
            "TURBOBOT_TRADE_FRACTION": TURBOBOT_TRADE_FRACTION,
            "TURBOBOT_LEVERAGE": TURBOBOT_LEVERAGE,
            "TURBOBOT_DAILY_TARGET_PCT": TURBOBOT_DAILY_TARGET_PCT,
            "TURBOBOT_DAILY_STOP_PCT": TURBOBOT_DAILY_STOP_PCT,
            "TURBOBOT_MAX_TRADES_PER_DAY": TURBOBOT_MAX_TRADES_PER_DAY,
            "TURBOBOT_COOLDOWN_AFTER_LOSS_SEC": TURBOBOT_COOLDOWN_AFTER_LOSS_SEC,
            "AUTO_DAILY_REPORTS_ENABLED": AUTO_DAILY_REPORTS_ENABLED,
            "BTC_DAILY_REPORT_TIME": f"{BTC_DAILY_REPORT_HOUR:02d}:{BTC_DAILY_REPORT_MINUTE:02d}",
            "TURBOBOT_DAILY_REPORT_TIME": f"{TURBOBOT_DAILY_REPORT_HOUR:02d}:{TURBOBOT_DAILY_REPORT_MINUTE:02d}",
            "AUTO_SUMMARY_STATE_FILE": AUTO_SUMMARY_STATE_FILE
        },
        "loss_guard_status": loss_guard_status_text(),
        "state": load_state(),
        "turbobot_state": tb_load_state()
    })


@app.route("/btc_scalp_status")
def btc_scalp_status_route():
    return jsonify({
        "enabled": BTC_SCALP_PAPER_ENABLED,
        "state_file": BTC_SCALP_STATE_FILE,
        "log_file": BTC_SCALP_LOG_FILE,
        "amount_btc": BTC_SCALP_PAPER_AMOUNT_BTC,
        "tp_points": BTC_SCALP_TP_POINTS,
        "trail_enabled": BTC_SCALP_TRAIL_ENABLED,
        "trail_trigger_points": BTC_SCALP_TRAIL_TRIGGER_POINTS,
        "trail_step_points": BTC_SCALP_TRAIL_STEP_POINTS,
        "state": btc_scalp_load_state()
    })


@app.route("/btc_scalp_daily_summary")
def btc_scalp_daily_summary_route():
    date_str = request.args.get("date")
    return "<pre>" + format_btc_scalp_daily_summary(date_str) + "</pre>"


@app.route("/send_btc_scalp_daily_summary", methods=["GET", "POST"])
def send_btc_scalp_daily_summary_route():
    date_str = request.args.get("date") or local_date_str()
    msg = format_btc_scalp_daily_summary(date_str)
    ok = send_telegram(msg, route="scalp")
    return jsonify({"ok": ok, "message": msg})



@app.route("/btc_scalp_total_summary")
def btc_scalp_total_summary_route():
    return "<pre>" + format_btc_scalp_total_summary() + "</pre>"


@app.route("/send_btc_scalp_total_summary", methods=["GET", "POST"])
def send_btc_scalp_total_summary_route():
    msg = format_btc_scalp_total_summary()
    ok = send_telegram(msg, route="scalp")
    return jsonify({"ok": ok, "message": msg})

@app.route("/reset_btc_scalp", methods=["GET", "POST"])
def reset_btc_scalp_route():
    st = btc_scalp_reset_state()
    send_telegram("LET OP - BTC Scalp paper-state handmatig gereset. Scalp positie staat nu FLAT.", route="scalp")
    return jsonify({"status": "reset", "btc_scalp_state": st})


@app.route("/trades")
def trades_route():
    limit = int(request.args.get("limit", "100"))
    data = load_trades()
    return jsonify({
        "count": len(data),
        "showing": min(limit, len(data)),
        "trades": data[-limit:]
    })



def load_auto_summary_state():
    try:
        if not os.path.exists(AUTO_SUMMARY_STATE_FILE):
            return {}
        with open(AUTO_SUMMARY_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_auto_summary_state(state):
    try:
        ensure_parent(AUTO_SUMMARY_STATE_FILE)
        with open(AUTO_SUMMARY_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def auto_summary_key(kind, date_str):
    return f"{kind}:{date_str}"


def auto_summary_already_sent(kind, date_str):
    state = load_auto_summary_state()
    return bool(state.get(auto_summary_key(kind, date_str)))


def mark_auto_summary_sent(kind, date_str):
    state = load_auto_summary_state()
    state[auto_summary_key(kind, date_str)] = local_time_str()
    state["last_sent_kind"] = kind
    state["last_sent_date"] = date_str
    state["last_sent_ts"] = now_ts()
    save_auto_summary_state(state)


def send_auto_daily_summary(kind, date_str=None, force=False):
    date_str = date_str or local_date_str()
    with AUTO_SUMMARY_LOCK:
        if not force and auto_summary_already_sent(kind, date_str):
            return False, f"{kind} dagoverzicht voor {date_str} was al verstuurd"

        if kind == "btc":
            msg = format_daily_summary(date_str)
        elif kind == "turbobot":
            msg = format_turbobot_daily_summary(date_str)
        elif kind == "both":
            btc_msg = format_daily_summary(date_str)
            turbo_msg = format_turbobot_daily_summary(date_str)
            ok1 = send_telegram(btc_msg)
            ok2 = send_telegram(turbo_msg)
            if ok1:
                mark_auto_summary_sent("btc", date_str)
            if ok2:
                mark_auto_summary_sent("turbobot", date_str)
            return bool(ok1 and ok2), "BTC en Turbobot dagoverzicht verstuurd"
        else:
            return False, f"Onbekend dagoverzicht type: {kind}"

        ok = send_telegram(msg)
        if ok:
            mark_auto_summary_sent(kind, date_str)
        return bool(ok), msg


def in_report_window(now_dt, hour, minute):
    scheduled = now_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
    delta_sec = (now_dt - scheduled).total_seconds()
    return 0 <= delta_sec < AUTO_DAILY_REPORT_WINDOW_MIN * 60


def auto_daily_report_loop():
    while True:
        try:
            if AUTO_DAILY_REPORTS_ENABLED:
                now = local_dt()
                date_str = now.strftime("%Y-%m-%d")
                if in_report_window(now, BTC_DAILY_REPORT_HOUR, BTC_DAILY_REPORT_MINUTE):
                    send_auto_daily_summary("btc", date_str=date_str, force=False)
                if in_report_window(now, TURBOBOT_DAILY_REPORT_HOUR, TURBOBOT_DAILY_REPORT_MINUTE):
                    send_auto_daily_summary("turbobot", date_str=date_str, force=False)
        except Exception as exc:
            try:
                print("auto_daily_report_loop error", repr(exc), flush=True)
            except Exception:
                pass
        time.sleep(max(10, AUTO_DAILY_REPORT_CHECK_SEC))


def start_auto_daily_report_thread():
    if not AUTO_DAILY_REPORTS_ENABLED:
        return
    # Voorkom dubbele scheduler bij Flask debug reloader.
    if os.environ.get("WERKZEUG_RUN_MAIN") == "false":
        return
    try:
        t = Thread(target=auto_daily_report_loop, daemon=True)
        t.start()
    except Exception as exc:
        try:
            print("start_auto_daily_report_thread error", repr(exc), flush=True)
        except Exception:
            pass


@app.route("/auto_summary_status")
def auto_summary_status_route():
    return jsonify({
        "enabled": AUTO_DAILY_REPORTS_ENABLED,
        "timezone": APP_TZ,
        "btc_daily_report_time": f"{BTC_DAILY_REPORT_HOUR:02d}:{BTC_DAILY_REPORT_MINUTE:02d}",
        "turbobot_daily_report_time": f"{TURBOBOT_DAILY_REPORT_HOUR:02d}:{TURBOBOT_DAILY_REPORT_MINUTE:02d}",
        "state_file": AUTO_SUMMARY_STATE_FILE,
        "state": load_auto_summary_state(),
    })


@app.route("/send_all_daily_summaries", methods=["GET", "POST"])
def send_all_daily_summaries_route():
    date_str = request.args.get("date") or local_date_str()
    force = bval(request.args.get("force", "false"))
    if force:
        ok1, msg1 = send_auto_daily_summary("btc", date_str=date_str, force=True)
        ok2, msg2 = send_auto_daily_summary("turbobot", date_str=date_str, force=True)
        return jsonify({"ok": bool(ok1 and ok2), "btc": ok1, "turbobot": ok2, "date": date_str})
    ok, msg = send_auto_daily_summary("both", date_str=date_str, force=False)
    return jsonify({"ok": ok, "message": msg, "date": date_str})


@app.route("/daily_summary")
def daily_summary_route():
    date_str = request.args.get("date")
    return "<pre>" + format_daily_summary(date_str) + "</pre>"


@app.route("/weekly_summary")
def weekly_summary_route():
    date_str = request.args.get("date")
    return "<pre>" + format_weekly_summary(date_str) + "</pre>"


@app.route("/send_daily_summary", methods=["GET", "POST"])
def send_daily_summary_route():
    date_str = request.args.get("date") or local_date_str()
    msg = format_daily_summary(date_str)
    ok = send_telegram(msg)
    if ok:
        mark_auto_summary_sent("btc", date_str)
    return jsonify({"ok": ok, "message": msg})


@app.route("/send_weekly_summary", methods=["GET", "POST"])
def send_weekly_summary_route():
    date_str = request.args.get("date")
    msg = format_weekly_summary(date_str)
    ok = send_telegram(msg)
    return jsonify({"ok": ok, "message": msg})



@app.route("/turbobot_status")
def turbobot_status_route():
    return jsonify({
        "state": tb_load_state(),
        "settings": {
            "start_capital": TURBOBOT_START_CAPITAL,
            "trade_fraction": TURBOBOT_TRADE_FRACTION,
            "leverage": TURBOBOT_LEVERAGE,
            "daily_target_pct": TURBOBOT_DAILY_TARGET_PCT,
            "daily_stop_pct": TURBOBOT_DAILY_STOP_PCT,
            "max_trades_per_day": TURBOBOT_MAX_TRADES_PER_DAY,
            "cooldown_after_loss_sec": TURBOBOT_COOLDOWN_AFTER_LOSS_SEC,
        }
    })


@app.route("/turbobot_trades")
def turbobot_trades_route():
    limit = int(request.args.get("limit", "100"))
    data = tb_load_events()
    return jsonify({"count": len(data), "showing": min(limit, len(data)), "events": data[-limit:]})


@app.route("/turbobot_daily_summary")
def turbobot_daily_summary_route():
    date_str = request.args.get("date")
    return "<pre>" + format_turbobot_daily_summary(date_str) + "</pre>"


@app.route("/send_turbobot_daily_summary", methods=["GET", "POST"])
def send_turbobot_daily_summary_route():
    date_str = request.args.get("date") or local_date_str()
    msg = format_turbobot_daily_summary(date_str)
    ok = send_telegram(msg)
    if ok:
        mark_auto_summary_sent("turbobot", date_str)
    return jsonify({"ok": ok, "message": msg})


@app.route("/reset_turbobot", methods=["GET", "POST"])
def reset_turbobot_route():
    s = tb_reset_state()
    send_telegram("LET OP - Turbobot paper-state handmatig gereset. Positie staat nu FLAT.")
    return jsonify({"status": "reset", "turbobot_state": s})

@app.route("/reset_state", methods=["GET", "POST"])
def reset_state_route():
    s = reset_state()
    send_telegram("LET OP - Bot-state handmatig gereset. Server denkt nu: geen botpositie open.")
    return jsonify({"status": "reset", "state": s})


@app.route("/send")
def send_test():
    ok = send_telegram("TEST BERICHT VAN RENDER BOT - V9.35 BTC TRENDBOT 1 + BTC SCALPBOT 1 APARTE TELEGRAM")
    return jsonify({"ok": ok, "status": "test gestuurd naar default/trend chat"})


@app.route("/send_btc_trend_test")
def send_btc_trend_test():
    ok = send_telegram("TEST BTC TRENDBOT 1 LIVE - Telegram route TREND", route="trend")
    return jsonify({"ok": ok, "route": "trend"})


@app.route("/send_btc_scalp_test")
def send_btc_scalp_test():
    ok = send_telegram("TEST BTC SCALPBOT 1 PAPER - Telegram route SCALP", route="scalp")
    return jsonify({"ok": ok, "route": "scalp"})


@app.route("/webhook", methods=["POST"])
def webhook():
    data = webhook_payload()

    # Turbobot eerst afvangen. Geen Kraken-orders, alleen paper/Telegram.
    if is_turbobot_alert(data):
        result = handle_turbobot_alert(data)
        return jsonify(result), 200

    # BTC Scalpbot 1 is paper/Telegram-only and must never enter Kraken live route.
    if clean(data.get("action")) in ["BTC_SCALP_BUY", "BTC_SCALP_EXIT"]:
        result = handle_btc_scalp_paper(data)
        return jsonify(result or {"event": "btc_scalp_no_action"}), 200

    action = clean(data.get("action"))
    price = clean(data.get("price"))
    bot = clean(data.get("bot"))
    ticker = clean(data.get("ticker"))
    reason = clean(data.get("exit_reason")) or clean(data.get("reason"))

    if not supported_bot(data):
        send_telegram(blocked_message(data, "Bot/ticker/action wordt niet herkend als ondersteunde Kraken BTC bot."))
        return "ok", 200

    # BTC Dual 1 Scalp paper wordt apart gemeten en voert nooit Kraken-orders uit.
    # De live Trend/Kraken route hieronder blijft ongewijzigd.
    handle_btc_scalp_paper(data)

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

        # Max botpositie bewaken op Render, onafhankelijk van Pine.
        remaining_btc = round(MAX_BOT_POSITION_BTC - bot_pos, 8)
        if remaining_btc < MIN_BTC_VOLUME:
            send_telegram(blocked_message(data, f"BUY geblokkeerd: max botpositie bereikt. Botpositie: {bot_pos:.8f} BTC, max: {MAX_BOT_POSITION_BTC:.8f} BTC."))
            return "ok", 200
        if volume_float > remaining_btc:
            volume_float = remaining_btc
            volume = f"{volume_float:.8f}"

        guard_blocked, guard_reason = buy_blocked_by_loss_guard(data, state)
        if guard_blocked:
            send_telegram(blocked_message(data, guard_reason))
            return "ok", 200

        res = kraken_buy(volume)
        if order_ok(res):
            oid = order_id(res)
            new_state = update_buy_state(volume_float, price, oid)

            append_trade_event({
                "type": "ORDER",
                "action": "BUY",
                "bot": bot,
                "ticker": ticker,
                "price": fval(price, None),
                "volume": volume_float,
                "order_id": oid,
                "reason": clean(data.get("reason")),
                "server_position_btc": new_state.get("bot_position_btc"),
                "avg_entry_price": new_state.get("avg_entry_price"),
                "open_trade_id": new_state.get("open_trade_id")
            })

            chopguard_log = loss_guard_buy_log_text(data, load_state())
            msg = buy_message(bot, ticker, price, volume, oid, clean(data.get("reason")), new_state, chopguard_log)
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

        # V9.29 BTC FULL POSITION SELL FIX
        # A BTC_SELL closes the complete server-tracked bot position.
        # Do not use Pine/payload volume here, because old Pine inputs can send 0.0004
        # while the current bot position is 0.004. The server state is leading.
        # Safety remains: never sell more than bot-owned position, Kraken balance, or MAX_BOT_POSITION_BTC.
        sell_volume = min(bot_pos, btc_balance, MAX_BOT_POSITION_BTC)

        if sell_volume < MIN_BTC_VOLUME:
            send_telegram(blocked_message(data, f"SELL genegeerd: onvoldoende verkoopbaar BTC. Botpositie: {bot_pos:.8f}, Kraken saldo: {btc_balance:.8f}, server-sell: {sell_volume:.8f}."))
            return "ok", 200

        entry_before = fval(state.get("avg_entry_price"), None)
        if entry_before is None:
            entry_before = fval(state.get("last_buy_price"), None)

        open_trade_id = state.get("open_trade_id")

        res = kraken_sell(f"{sell_volume:.8f}")
        if order_ok(res):
            oid = order_id(res)
            new_state = update_sell_state(sell_volume, price, oid, data)

            sell_price = fval(price, None)
            trade_points = None
            gross_eur = None
            net_points_est = None
            net_eur_est = None
            result = None
            if entry_before is not None and sell_price is not None:
                trade_points = sell_price - entry_before
                gross_eur = trade_points * sell_volume
                net_points_est = trade_points - ROUND_TRIP_COST_POINTS
                net_eur_est = net_points_est * sell_volume
                result = result_from_points(trade_points)

            append_trade_event({
                "type": "ORDER",
                "action": "SELL",
                "bot": bot,
                "ticker": ticker,
                "price": sell_price,
                "volume": sell_volume,
                "order_id": oid,
                "reason": reason,
                "server_position_btc": new_state.get("bot_position_btc"),
                "open_trade_id": open_trade_id
            })

            append_trade_event({
                "type": "CLOSED_TRADE",
                "bot": bot,
                "ticker": ticker,
                "entry_price": entry_before,
                "exit_price": sell_price,
                "volume": sell_volume,
                "points": trade_points,
                "gross_eur": gross_eur,
                "net_points_est": net_points_est,
                "net_eur_est": net_eur_est,
                "result": result,
                "buy_order_id": state.get("last_buy_order_id"),
                "sell_order_id": oid,
                "reason": reason,
                "open_trade_id": open_trade_id,
                "pine_entry_price": fval(get_pine_entry(data), None),
                "pine_entry_diff": new_state.get("last_pine_entry_diff"),
                "warning": new_state.get("last_pine_result_warning")
            })

            guard_info = maybe_arm_loss_guard_after_sell(data)

            msg = sell_message(
                bot=bot,
                ticker=ticker,
                price=price,
                volume=sell_volume,
                oid=oid,
                reason=reason,
                state=new_state,
                entry_before=entry_before,
                pine_entry=get_pine_entry(data)
            )
            if guard_info and guard_info.get("armed"):
                msg += (
                    "\n\nRender chopbescherming actief"
                    f"\nReden: {guard_info.get('reason')}"
                    f"\nCooldown: {LOSS_GUARD_COOLDOWN_CANDLES} candles"
                    f"\nTot: {local_time_str(guard_info.get('until_ts'))}"
                    "\nNormale BUY wordt tijdelijk geblokkeerd."
                    "\nRocket/breakout/HH-HL/reclaim override blijft toegestaan."
                )
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


start_auto_daily_report_thread()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
