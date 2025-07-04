
# bot.py — motor de trading multi-par con logs de decisiones en SQLite
import os, sqlite3, threading, time
from datetime import datetime

import ccxt
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

# ───────────────────────── CONFIG ───────────────────────── #
API_KEY       = os.getenv("BINANCE_API_KEY")
API_SECRET    = os.getenv("BINANCE_API_SECRET")
USE_TESTNET   = False  # Spot real (Binance no tiene testnet pública)
BASE_CURRENCY = os.getenv("BASE_CURRENCY", "EUR")
TICKERS       = os.getenv("TICKERS", "BTC/EUR,ETH/EUR,ADA/EUR,BNB/EUR,SOL/EUR,DOT/EUR").split(",")
INTERVAL      = os.getenv("INTERVAL", "1m")
RSI_PERIOD    = int(os.getenv("RSI_PERIOD", 14))
SMA_PERIOD    = int(os.getenv("SMA_PERIOD", 20))
TRADE_FRACTION= float(os.getenv("TRADE_FRACTION", 1 / max(len(TICKERS), 1)))
RSI_LOW       = float(os.getenv("RSI_LOW", 30))
RSI_HIGH      = float(os.getenv("RSI_HIGH", 70))
TAKE_PROFIT_PCT = float(os.getenv("TAKE_PROFIT_PCT", 0.02))
STOP_LOSS_PCT = 0.02  # 2%
# Tamaño máximo de logs.db en MB (0 desactiva el control)
LOG_DB_MAX_MB = float(os.getenv("LOG_DB_MAX_MB", 0))



# ────────────── DB HELPERS ──────────────
LOG_DB = "logs.db"

def _enforce_log_size(conn):
    """Reduce la tabla si logs.db supera LOG_DB_MAX_MB"""
    if not LOG_DB_MAX_MB:
        return
    try:
        max_bytes = LOG_DB_MAX_MB * 1024 * 1024
        if os.path.getsize(LOG_DB) <= max_bytes:
            return
        # Borra registros antiguos en bloques hasta reducir el tamaño
        while os.path.getsize(LOG_DB) > max_bytes:
            conn.execute(
                "DELETE FROM log_evaluaciones WHERE rowid IN "
                "(SELECT rowid FROM log_evaluaciones ORDER BY rowid ASC LIMIT 500)"
            )
            conn.commit()
        conn.execute("VACUUM")
        conn.commit()
    except Exception:
        pass

def ensure_db():
    conn = sqlite3.connect(LOG_DB)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS log_evaluaciones ("
        "datetime TEXT, pair TEXT, close REAL, rsi REAL, sma REAL, "
        "decision TEXT, motivo TEXT)"
    )
    conn.commit()
    conn.close()

def insert_log(dt, pair, close, rsi, sma, decision, motivo):
    conn = sqlite3.connect(LOG_DB)
    conn.execute(
        "INSERT INTO log_evaluaciones (datetime, pair, close, rsi, sma, decision, motivo) VALUES (?,?,?,?,?,?,?)",
        (dt, pair, close, rsi, sma, decision, motivo)
    )
    conn.commit()
    _enforce_log_size(conn)
    conn.close()

# Aseguramos base de datos de logs
ensure_db()
# ───────────────────── CONEXIÓN BINANCE ─────────────────── #
binance = ccxt.binance({
    "apiKey": API_KEY,
    "secret": API_SECRET,
    "enableRateLimit": True,
    "options": {"defaultType": "spot", "adjustForTimeDifference": True}
})
binance.has["fetchCurrencies"] = False
binance.load_markets()

# ───────────────────── SALDO INICIAL ────────────────────── #
def get_balance():
    bal   = binance.fetch_balance({"type": "spot"})
    total = float(bal["total"].get(BASE_CURRENCY, 0))
    free  = float(bal["free"].get(BASE_CURRENCY,  0))
    return total, free

capital_total, capital_free = get_balance()

# ───────────────────── STATE POR PAR ────────────────────── #
state = { t: dict(position=False, amount=0.0, entry=0.0, locked=0.0, unreal=0.0)
          for t in TICKERS }

# ───────────────── CÁLCULO DE BENEFICIO REAL ─────────────── #
def calculate_total_profit():
    """Devuelve el beneficio realizado sumando las operaciones cerradas."""
    profit = 0.0
    for pair in TICKERS:
        try:
            trades = binance.fetch_my_trades(pair)
        except Exception:
            continue
        trades.sort(key=lambda t: t['timestamp'])
        pos_amt = 0.0
        pos_cost = 0.0
        for tr in trades:
            qty = float(tr['amount'])
            price = float(tr['price'])
            if tr['side'] == 'buy':
                pos_amt += qty
                pos_cost += qty * price
            else:  # venta
                if pos_amt <= 0:
                    continue
                avg_cost = pos_cost / pos_amt
                sold = min(qty, pos_amt)
                profit += price * sold - avg_cost * sold
                pos_amt -= sold
                pos_cost -= avg_cost * sold
    return profit

# Nueva función para obtener beneficio por fecha
def calculate_profit_series():
    """Devuelve una lista de objetos {date, profit} agregados por día."""
    from collections import defaultdict
    series = defaultdict(float)
    first_day = None
    last_day = None
    for pair in TICKERS:
        try:
            trades = binance.fetch_my_trades(pair)
        except Exception:
            continue
        trades.sort(key=lambda t: t['timestamp'])
        if trades:
            d0 = datetime.fromtimestamp(trades[0]['timestamp']/1000).date()
            d1 = datetime.fromtimestamp(trades[-1]['timestamp']/1000).date()
            first_day = d0 if first_day is None else min(first_day, d0)
            last_day = d1 if last_day is None else max(last_day, d1)
        pos_amt = 0.0
        pos_cost = 0.0
        for tr in trades:
            qty = float(tr['amount'])
            price = float(tr['price'])
            if tr['side'] == 'buy':
                pos_amt += qty
                pos_cost += qty * price
            else:  # venta
                if pos_amt <= 0:
                    continue
                avg_cost = pos_cost / pos_amt
                sold = min(qty, pos_amt)
                profit = price * sold - avg_cost * sold
                day = datetime.fromtimestamp(tr['timestamp']/1000).strftime('%Y-%m-%d')
                series[day] += profit
                pos_amt -= sold
                pos_cost -= avg_cost * sold

    if first_day is None or last_day is None:
        return []

    result = []
    for d in pd.date_range(start=first_day, end=last_day):
        ds = d.strftime('%Y-%m-%d')
        result.append({"date": ds, "profit": series.get(ds, 0.0)})

    # Añadir PnL no realizado del día actual para mostrar pérdidas abiertas
    open_profit = 0.0
    for pair in TICKERS:
        try:
            trades = binance.fetch_my_trades(pair)
        except Exception:
            continue
        trades.sort(key=lambda t: t['timestamp'])
        pos_amt = 0.0
        pos_cost = 0.0
        for tr in trades:
            qty = float(tr['amount'])
            price = float(tr['price'])
            if tr['side'] == 'buy':
                pos_amt += qty
                pos_cost += qty * price
            else:
                if pos_amt <= 0:
                    continue
                avg_cost = pos_cost / pos_amt
                sold = min(qty, pos_amt)
                pos_amt -= sold
                pos_cost -= avg_cost * sold
        if pos_amt > 0:
            try:
                last_price = binance.fetch_ticker(pair)['last']
            except Exception:
                last_price = 0.0
            open_profit += last_price * pos_amt - pos_cost

    if open_profit != 0:
        today = datetime.now().strftime('%Y-%m-%d')
        # Si ya existe una entrada para hoy, la sumamos
        for entry in result:
            if entry['date'] == today:
                entry['profit'] += open_profit
                break
        else:
            result.append({"date": today, "profit": open_profit})

    return result

# ───────────────────── INDICADORES ─────────────────────── #
def fetch_df(pair):
    ohlcv = binance.fetch_ohlcv(pair, timeframe=INTERVAL, limit=150)
    df = pd.DataFrame(ohlcv, columns=["ts","open","high","low","close","vol"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms")
    df.set_index("ts", inplace=True)
    df["SMA"] = df["close"].rolling(SMA_PERIOD).mean()
    delta = df["close"].diff()
    gain  = delta.clip(lower=0)
    loss  = -delta.clip(upper=0)
    rs    = gain.rolling(RSI_PERIOD).mean() / loss.rolling(RSI_PERIOD).mean()
    df["RSI"] = 100 - 100/(1+rs)
    return df

# ─────────────────── ESTRATEGIA + MOTIVO ────────────────── #
def friendly_eval(rsi, sma, price, pos_open, entry=0.0):
    ok = lambda c: "✅" if c else "❌"
    f2 = lambda x: f"{x:,.2f}".replace(",", " ")
    rsi_lt, rsi_gt = rsi < RSI_LOW, rsi > RSI_HIGH
    p_gt_sma, p_lt_sma = price > sma, price < sma
    if not pos_open:
        if rsi_lt and p_gt_sma:
            mot = (f"{ok(rsi_lt)} RSI {f2(rsi)}<{RSI_LOW} • {ok(p_gt_sma)} Precio {f2(price)}>SMA {f2(sma)} → Comprar")
            return "buy", mot
        mot = (f"{ok(rsi_lt)} RSI {f2(rsi)}<{RSI_LOW} • {ok(rsi_gt)} RSI {f2(rsi)}>{RSI_HIGH} • "
               f"{ok(p_gt_sma)} Precio {f2(price)}>SMA {f2(sma)} • {ok(p_lt_sma)} Precio {f2(price)}<SMA {f2(sma)} → Esperar")
        return "hold", mot
    stop_loss = entry > 0 and price <= entry * (1 - STOP_LOSS_PCT)
    take_profit = entry > 0 and price >= entry * (1 + TAKE_PROFIT_PCT)
    exit_cond = stop_loss or take_profit
    if exit_cond:
        mot = ((f"{ok(stop_loss)} Stop Loss ({f2(price)} ≤ {f2(entry*(1-STOP_LOSS_PCT))})" if stop_loss else "") +
               (" o " if stop_loss and take_profit else "") +
               (f"{ok(take_profit)} Take Profit ({f2(price)} ≥ {f2(entry*(1+TAKE_PROFIT_PCT))})" if take_profit else "") +
               " → Cerrar")
        return "sell", mot
    mot = (f"{ok(not exit_cond)} Esperando beneficio o stop loss → Mantener")
    return "hold", mot

# ─────────────────── EJECUCIÓN DE TRADE ─────────────────── #
def execute(pair, decision, price, rsi, sma, motivo):
    global capital_free
    st = state[pair]
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # --- comprar ---
    if decision == "buy" and not st["position"] and capital_free > 0:
        eur_to_use = capital_free * TRADE_FRACTION * 0.999
        if eur_to_use < 1e-6: return
        try:
            if not USE_TESTNET:
                order = binance.create_order(pair, "market", "buy", None, None, {"quoteOrderQty": eur_to_use})
                if order.get("status") not in ("closed", "filled"): return
                st["amount"] = float(order.get("filled", order.get("amount", 0)))
            else:
                st["amount"] = eur_to_use / price
            st["locked"] = float(order.get("cost", eur_to_use))
            capital_free -= st["locked"]
            st.update(position=True, entry=price)
        except Exception as e:
            print("Buy error", pair, e)
            return
    # --- vender / cerrar ---
    elif decision == "sell" and st["position"]:
        try:
            if not USE_TESTNET:
                base = pair.split('/')[0]
                bal = binance.fetch_balance()["free"].get(base, 0)
                if bal < 1e-6: return
                order = binance.create_order(pair, "market", "sell", bal)
                if not order or order.get("status") not in ("closed", "filled"): return
                proceeds = float(order.get("cost", 0))
            else:
                proceeds = st["amount"] * price
            capital_free += proceeds
            st.update(position=False, amount=0.0, entry=0.0, locked=0.0, unreal=0.0)
        except Exception as e:
            print("Sell error", pair, e)
            return
    if st.get("position"):
        st["unreal"] = (price - st["entry"]) * st["amount"]
    # Registrar evaluación en base de datos
    insert_log(now, pair, float(price), float(rsi), float(sma), decision, motivo)

# ─────────────────── CALLBACK WEB + LOOP ─────────────────── #
_gui_callback = lambda: None

def schedule_callback(func):
    global _gui_callback; _gui_callback = func

def _worker():
    while True:
        try:
            for pair in TICKERS:
                df = fetch_df(pair)
                rsi, sma = df["RSI"].iloc[-1], df["SMA"].iloc[-1]
                price = df["close"].iloc[-1]
                decision, motivo = friendly_eval(rsi, sma, price, state[pair]["position"], state[pair].get("entry", 0.0))
                execute(pair, decision, price, rsi, sma, motivo)
            _gui_callback()
        except Exception as exc:
            print("Loop error:", exc)
        time.sleep(60)

threading.Thread(target=_worker, daemon=True).start()

