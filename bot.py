
# bot.py — motor de trading multi-par con logs de decisiones en CSV (sin historial CSV)
import os, csv, threading, time
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
TICKERS       = os.getenv("TICKERS", "BTC/EUR,ETH/EUR,ADA/EUR").split(",")
INTERVAL      = os.getenv("INTERVAL", "1m")
RSI_PERIOD    = int(os.getenv("RSI_PERIOD", 14))
SMA_PERIOD    = int(os.getenv("SMA_PERIOD", 20))
TRADE_FRACTION= float(os.getenv("TRADE_FRACTION", 1 / max(len(TICKERS), 1)))
STOP_LOSS_PCT = 0.02  # 2%

# ───────────────────── CSV HELPERS ─────────────────────── #
LOG_CSV = "log_evaluaciones.csv"
HEAD_LOG = ["datetime", "pair", "Close", "RSI", "SMA", "decision", "motivo"]

def ensure_csv(path, header):
    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        with open(path, "w", newline='', encoding="utf-8") as f:
            csv.writer(f).writerow(header)

def append_csv(path, row):
    with open(path, "a", newline='', encoding="utf-8") as f:
        csv.writer(f).writerow(row)

# Aseguramos cabecera del log
ensure_csv(LOG_CSV, HEAD_LOG)

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
benefit_total = 0.0

# ───────────────────── STATE POR PAR ────────────────────── #
state = { t: dict(position=False, amount=0.0, entry=0.0, locked=0.0, unreal=0.0)
          for t in TICKERS }

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
    rsi_lt30, rsi_gt70 = rsi < 30, rsi > 70
    p_gt_sma, p_lt_sma = price > sma, price < sma
    if not pos_open:
        if rsi_lt30 and p_gt_sma:
            mot = (f"{ok(rsi_lt30)} RSI {f2(rsi)}<30 • {ok(p_gt_sma)} Precio {f2(price)}>SMA {f2(sma)} → Comprar")
            return "buy", mot
        if rsi_gt70 and p_lt_sma:
            mot = (f"{ok(rsi_gt70)} RSI {f2(rsi)}>70 • {ok(p_lt_sma)} Precio {f2(price)}<SMA {f2(sma)} → Vender")
            return "sell", mot
        mot = (f"{ok(rsi_lt30)} RSI {f2(rsi)}<30 • {ok(rsi_gt70)} RSI {f2(rsi)}>70 • "
               f"{ok(p_gt_sma)} Precio {f2(price)}>SMA {f2(sma)} • {ok(p_lt_sma)} Precio {f2(price)}<SMA {f2(sma)} → Esperar")
        return "hold", mot
    stop_loss = entry > 0 and price <= entry * (1 - STOP_LOSS_PCT)
    exit_cond = rsi_gt70 or p_lt_sma or stop_loss
    if exit_cond:
        mot = ((f"{ok(rsi_gt70)} RSI {f2(rsi)}>70 o " if rsi_gt70 else "") +
               (f"{ok(p_lt_sma)} Precio {f2(price)}<SMA {f2(sma)} o " if p_lt_sma else "") +
               (f"{ok(stop_loss)} Stop Loss ({f2(price)} ≤ {f2(entry*(1-STOP_LOSS_PCT))}) → Cerrar" if stop_loss else ""))
        return "sell", mot
    mot = (f"{ok(rsi_gt70)} RSI {f2(rsi)}>70 y {ok(p_lt_sma)} Precio {f2(price)}<SMA {f2(sma)} → Mantener")
    return "hold", mot

# ─────────────────── EJECUCIÓN DE TRADE ─────────────────── #
def execute(pair, decision, price, rsi, sma, motivo):
    global capital_free, benefit_total
    st = state[pair]
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # --- comprar ---
    if decision == "buy" and not st["position"] and capital_free > 0:
        eur_to_use = capital_free * 0.999
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
            profit = proceeds - st.get("locked", 0)
            capital_free += proceeds
            benefit_total += profit
            st.update(position=False, amount=0.0, entry=0.0, locked=0.0, unreal=0.0)
        except Exception as e:
            print("Sell error", pair, e)
            return
    if st.get("position"):
        st["unreal"] = (price - st["entry"]) * st["amount"]
    # Registrar evaluación en CSV
    append_csv(LOG_CSV, [now, pair, f"{price:.8f}", f"{rsi:.2f}", f"{sma:.2f}", decision, motivo])

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

