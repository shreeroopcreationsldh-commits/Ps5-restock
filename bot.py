import os
import sqlite3
import logging
from datetime import datetime, timezone
from flask import Flask, request, jsonify
import requests
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
ALERT_SECRET = os.getenv("ALERT_SECRET", "").strip()
PIN = os.getenv("LUDHIANA_PIN", "141001").strip()

if not BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is missing in .env")

DB = os.getenv("DATABASE_PATH", "ps5bot.db")
app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

def db():
    conn = sqlite3.connect(DB)
    conn.execute("""CREATE TABLE IF NOT EXISTS state (
        product_key TEXT PRIMARY KEY,
        available INTEGER NOT NULL,
        last_price TEXT,
        last_seen TEXT
    )""")
    return conn

def telegram_send(text, chat_id=None):
    target = chat_id or CHAT_ID
    if not target:
        logging.warning("No CHAT_ID configured.")
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    r = requests.post(url, json={
        "chat_id": target,
        "text": text,
        "disable_web_page_preview": False
    }, timeout=20)
    r.raise_for_status()
    return True

def process_alert(data):
    # Expected JSON:
    # {
    #   "product_key":"amazon_ps5_slim_disc",
    #   "store":"Amazon",
    #   "name":"PS5 Slim Disc Edition",
    #   "price":"â¹49,990",
    #   "url":"https://...",
    #   "available":true,
    #   "deliverable_pin":"141001",
    #   "delivery":"Tomorrow"
    # }
    required = ["product_key", "store", "name", "url", "available"]
    missing = [x for x in required if x not in data]
    if missing:
        return False, f"Missing fields: {', '.join(missing)}"

    available = bool(data["available"])
    pin_ok = str(data.get("deliverable_pin", PIN)) == PIN
    effective = available and pin_ok

    conn = db()
    row = conn.execute(
        "SELECT available FROM state WHERE product_key=?",
        (data["product_key"],)
    ).fetchone()
    previous = bool(row[0]) if row else False

    now = datetime.now(timezone.utc).isoformat()
    conn.execute("""
        INSERT INTO state(product_key, available, last_price, last_seen)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(product_key) DO UPDATE SET
          available=excluded.available,
          last_price=excluded.last_price,
          last_seen=excluded.last_seen
    """, (data["product_key"], int(effective), data.get("price", ""), now))
    conn.commit()
    conn.close()

    # Alert only on UNAVAILABLE -> AVAILABLE transition.
    if effective and not previous:
        text = (
            "ð¨ PS5 AVAILABLE!\n\n"
            f"ð® {data['name']}\n"
            f"ðª {data['store']}\n"
            f"ð° {data.get('price', 'Check product page')}\n"
            f"ð Deliverable to {PIN}\n"
            f"ð {data.get('delivery', 'Check product page')}\n\n"
            f"ð {data['url']}\n\n"
            f"â° {datetime.now().strftime('%d-%m-%Y %I:%M:%S %p')}"
        )
        telegram_send(text)
        return True, "Alert sent"

    return True, "State updated; no new alert"

@app.post("/stock-alert")
def stock_alert():
    if ALERT_SECRET and request.headers.get("X-Alert-Secret") != ALERT_SECRET:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    ok, message = process_alert(data)
    return jsonify({"ok": ok, "message": message})

@app.get("/")
def home():
    return "PS5 Telegram alert bot is running."

@app.get("/health")
def health():
    return jsonify({"ok": True, "pin": PIN})

def telegram_updates():
    offset = None
    while True:
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
            params = {"timeout": 25}
            if offset is not None:
                params["offset"] = offset
            r = requests.get(url, params=params, timeout=35)
            r.raise_for_status()
            updates = r.json().get("result", [])
            for update in updates:
                offset = update["update_id"] + 1
                msg = update.get("message", {})
                text = msg.get("text", "")
                chat_id = str(msg.get("chat", {}).get("id", ""))

                if text == "/start":
                    if not CHAT_ID:
                        telegram_send(
                            f"â PS5 Restock Bot connected!\n\n"
                            f"ð Monitoring delivery PIN: {PIN}\n"
                            f"ð® Amazon + Flipkart source integration is ready to be connected.",
                            chat_id
                        )
                    else:
                        telegram_send(
                            f"â Bot is running.\nð Delivery PIN: {PIN}\n"
                            "Use /status to check the bot.",
                            chat_id
                        )

                elif text == "/status":
                    conn = db()
                    count = conn.execute("SELECT COUNT(*) FROM state").fetchone()[0]
                    available = conn.execute(
                        "SELECT COUNT(*) FROM state WHERE available=1"
                    ).fetchone()[0]
                    conn.close()
                    telegram_send(
                        f"ð¤ PS5 Restock Bot\n\n"
                        f"ð PIN: {PIN}\n"
                        f"ð¦ Tracked products: {count}\n"
                        f"ð¢ Currently available: {available}\n"
                        f"ð {datetime.now().strftime('%d-%m-%Y %I:%M:%S %p')}",
                        chat_id
                    )

                elif text == "/check":
                    telegram_send(
                        "ð Manual check requested.\n"
                        "The Telegram layer is working; stock checks require an authorized Amazon/Flipkart feed or API connection.",
                        chat_id
                    )

                elif text == "/help":
                    telegram_send(
                        "/start â connect bot\n"
                        "/status â bot status\n"
                        "/check â manual check\n"
                        "/help â commands",
                        chat_id
                    )
        except Exception as e:
            logging.exception("Telegram polling error: %s", e)

if __name__ == "__main__":
    import threading
    threading.Thread(target=telegram_updates, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
