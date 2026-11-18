"""Symulator handlu kontraktami USDT na BingX."""

import argparse
import json
import logging
import math
import sqlite3
import time
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import urlopen

BASE = "https://open-api.bingx.com/openApi/swap"
BAR_MS = 15 * 60 * 1000
FEE = 0.0005
SLIPPAGE = 0.0002
STOP = 0.01
TARGET = 0.02
MAX_POSITIONS = 3
DAILY_LOSS = 0.02


def number(value):
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"Nieprawidłowa wartość rynkowa: {value}")
    return result


def get(path, **params):
    url = f"{BASE}/{path}?{urlencode(params)}"
    with urlopen(url, timeout=10) as response:
        payload = json.load(response)
    if str(payload.get("code")) != "0":
        raise ValueError(f"BingX {payload.get('code')}: {payload.get('msg')}")
    return payload["data"]


def universe(limit):
    ranking = []
    for ticker in get("v2/quote/ticker"):
        symbol = ticker["symbol"]
        if symbol.endswith("-USDT"):
            volume = float(ticker.get("quoteVolume") or 0)
            if math.isfinite(volume) and volume > 0:
                ranking.append((volume, symbol))
    return [symbol for _, symbol in sorted(ranking, reverse=True)[:limit]]


def candles(symbol, boundary):
    rows = get("v3/quote/klines", symbol=symbol, interval="15m", limit=120)
    closes = {
        int(row["time"]): number(row["close"])
        for row in rows
        if int(row["time"]) < boundary
    }
    expected = range(boundary - 100 * BAR_MS, boundary, BAR_MS)
    return [closes[stamp] for stamp in expected]


def ema(values, period):
    result = values[0]
    for value in values[1:]:
        result += (value - result) * 2 / (period + 1)
    return result


def signal(closes):
    previous = ema(closes[:-1], 9) - ema(closes[:-1], 21)
    current = ema(closes, 9) - ema(closes, 21)
    if previous <= 0 < current:
        return 1
    if previous >= 0 > current:
        return -1
    return 0


def process(db, symbol, boundary, closes, initial):
    day = datetime.fromtimestamp(boundary / 1000, timezone.utc).date().isoformat()
    price = closes[-1]
    direction = signal(closes)
    with db:
        claimed = db.execute(
            "INSERT INTO scan VALUES (?, ?) ON CONFLICT(symbol) DO UPDATE "
            "SET boundary=excluded.boundary WHERE excluded.boundary > scan.boundary",
            (symbol, boundary),
        )
        if not claimed.rowcount:
            return

        position = db.execute(
            "SELECT side, entry, size FROM positions WHERE symbol=?", (symbol,)
        ).fetchone()
        if position:
            side, entry, size = position
            move = side * (price / entry - 1)
            reason = (
                "stop" if move <= -STOP else "target" if move >= TARGET
                else "reverse" if direction == -side else None
            )
            if reason:
                fill = price * (1 - side * SLIPPAGE)
                pnl = size * (side * (fill - entry) - FEE * (fill + entry))
                db.execute("DELETE FROM positions WHERE symbol=?", (symbol,))
                db.execute(
                    "INSERT INTO trades VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (symbol, day, boundary, side, reason, entry, fill, size, pnl),
                )
                logging.info("%s zamknięcie %s | wynik %+.2f USDT", symbol, reason, pnl)
            return

        if not direction:
            return
        prior, today = db.execute(
            "SELECT COALESCE(SUM(CASE WHEN day < ? THEN pnl END),0), "
            "COALESCE(SUM(CASE WHEN day = ? THEN pnl END),0) FROM trades",
            (day, day),
        ).fetchone()
        used, count = db.execute(
            "SELECT COALESCE(SUM(entry * size),0), COUNT(*) FROM positions"
        ).fetchone()
        start = initial + prior
        balance = start + today
        if start <= 0 or today <= -start * DAILY_LOSS or count >= MAX_POSITIONS:
            return
        notional = min(balance * 0.10, balance - used)
        if notional <= 0:
            return
        entry = price * (1 + direction * SLIPPAGE)
        db.execute(
            "INSERT INTO positions VALUES (?, ?, ?, ?)",
            (symbol, direction, entry, notional / entry),
        )
        logging.info(
            "%s otwarcie %s | %.2f USDT | cena %.8g",
            symbol, "LONG" if direction == 1 else "SHORT", notional, entry,
        )


def open_db(path, initial):
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY, initial REAL);
        CREATE TABLE IF NOT EXISTS scan (symbol TEXT PRIMARY KEY, boundary INTEGER);
        CREATE TABLE IF NOT EXISTS positions (
            symbol TEXT PRIMARY KEY, side INTEGER, entry REAL, size REAL);
        CREATE TABLE IF NOT EXISTS trades (
            symbol TEXT, day TEXT, boundary INTEGER, side INTEGER, reason TEXT,
            entry REAL, exit REAL, size REAL, pnl REAL);
    """)
    with db:
        db.execute("INSERT OR IGNORE INTO settings VALUES (1, ?)", (initial,))
    return db, db.execute("SELECT initial FROM settings WHERE id=1").fetchone()[0]


def scan(db, initial, limit):
    # Daj giełdzie kilka sekund na opublikowanie zamkniętej świecy.
    boundary = int((time.time() - 5) * 1000) // BAR_MS * BAR_MS
    positions = [row[0] for row in db.execute("SELECT symbol FROM positions ORDER BY symbol")]
    try:
        markets = universe(limit)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        logging.warning("Ranking BingX niedostępny: %s", exc)
        markets = []

    checked = 0
    for symbol in dict.fromkeys(positions + markets):
        last = db.execute(
            "SELECT boundary FROM scan WHERE symbol=?", (symbol,)
        ).fetchone()
        if last and last[0] >= boundary:
            checked += 1
            continue
        try:
            closes = candles(symbol, boundary)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            logging.warning("%s | brak kompletnych świec: %s", symbol, exc)
            continue
        process(db, symbol, boundary, closes, initial)
        checked += 1
        time.sleep(0.15)

    if checked:
        count = db.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
        pnl = db.execute("SELECT COALESCE(SUM(pnl), 0) FROM trades").fetchone()[0]
        logging.info(
            "Pary sprawdzone: %d | otwarte: %d | wynik zrealizowany: %+.2f USDT",
            checked, count, pnl,
        )
    return bool(markets and checked)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="wykonaj jeden skan")
    parser.add_argument("--pairs", type=int, choices=range(1, 51), default=20,
                        metavar="1-50")
    parser.add_argument("--capital", type=float, default=1000, help="saldo nowej bazy")
    parser.add_argument("--db", default="paper.sqlite3", help="plik stanu i historii")
    args = parser.parse_args()
    if not math.isfinite(args.capital) or args.capital <= 0:
        parser.error("--capital musi być dodatnią, skończoną liczbą")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    db, initial = open_db(args.db, args.capital)
    try:
        while True:
            success = scan(db, initial, args.pairs)
            if args.once:
                return 0 if success else 1
            time.sleep(60)
    except KeyboardInterrupt:
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
