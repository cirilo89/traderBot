# bot.py — motor de trading multi-par (sin GUI)
import os, csv, threading, time
from datetime import datetime

import ccxt
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

# ───────────────────────── CONFIG ───────────────────────── #
API_KEY    = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")

USE_TESTNET     = False                # Spot real (Binance no tiene testnet pública)
BASE_CURRENCY   = os.getenv("BASE_CURRENCY", "EUR")
TICKERS         = os.getenv("TICKERS", "BTC/EUR,ETH/EUR,ADA/EUR").split(",")

INTERVAL        = os.getenv("INTERVAL", "1m")
RSI_PERIOD      = int(os.getenv("RSI_PERIOD", 14))
SMA_PERIOD      = int(os.getenv("SMA_PERIOD", 20))
TRADE_FRACTION  = float(os.getenv("TRADE_FRACTION", 1 / max(len(TICKERS), 1)))

LOG_CSV   = "log_evaluaciones.csv"
TRADES_CSV= "historial_trades.csv"

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
    bal = binance.fetch_balance({"type": "spot"})
    total = float(bal["total"].get(BASE_CURRENCY, 0))
    free  = float(bal["free"].get(BASE_CURRENCY,  0))
    return total, free

capital_total, capital_free = get_balance()
benefit_total = 0.0

# ───────────────────── STATE POR PAR ────────────────────── #
state = {
    t: dict(position=False, amount=0.0, entry=0.0,
            locked=0.0, unreal=0.0)
    for t in TICKERS
}

# ───────────────────── CSV HELPERS ──────────────────────── #
HEAD_LOG   = ["datetime","pair","Close","RSI","SMA","decision","motivo"]
HEAD_TRADE = ["datetime","pair","Close","RSI","SMA","decision"]

def ensure_csv(path, header):
    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        with open(path, "w", newline='', encoding="utf-8") as f:
            csv.writer(f).writerow(header)

def append_csv(path, row):
    with open(path, "a", newline='', encoding="utf-8") as f:
        csv.writer(f).writerow(row)

ensure_csv(LOG_CSV, HEAD_LOG)
ensure_csv(TRADES_CSV, HEAD_TRADE)

# ───────────────────── INDICADORES ─────────────────────–– #
def fetch_df(pair):
    ohlcv = binance.fetch_ohlcv(pair, timeframe=INTERVAL, limit=150)
    df = pd.DataFrame(ohlcv, columns=["ts","open","high","low","close","vol"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms")
    df.set_index("ts", inplace=True)

    df["SMA"] = df["close"].rolling(SMA_PERIOD).mean()
    delta = df["close"].diff()
    gain  = delta.clip(lower=0)
    loss  = -delta.clip(upper=0)
    rs = gain.rolling(RSI_PERIOD).mean() / loss.rolling(RSI_PERIOD).mean()
    df["RSI"] = 100 - 100/(1+rs)
    return df

# ─────────────────── ESTRATEGIA + MOTIVO ────────────────── #
def friendly_eval(rsi, sma, price, pos_open):
    ok = lambda c: "✅" if c else "❌"
    f2 = lambda x: f"{x:,.2f}".replace(",", " ")

    rsi_lt30, rsi_gt70 = rsi < 30, rsi > 70
    p_gt_sma, p_lt_sma = price > sma, price < sma

    if not pos_open:
        if rsi_lt30 and p_gt_sma:
            mot = (f"{ok(rsi_lt30)} RSI {f2(rsi)}<30 • "
                   f"{ok(p_gt_sma)} Precio {f2(price)}>SMA {f2(sma)} → Comprar")
            return "buy", mot
        if rsi_gt70 and p_lt_sma:
            mot = (f"{ok(rsi_gt70)} RSI {f2(rsi)}>70 • "
                   f"{ok(p_lt_sma)} Precio {f2(price)}<SMA {f2(sma)} → Vender")
            return "sell", mot
        mot = (f"{ok(rsi_lt30)} RSI {f2(rsi)}<30 • {ok(rsi_gt70)} RSI {f2(rsi)}>70 • "
               f"{ok(p_gt_sma)} Precio {f2(price)}>SMA {f2(sma)} • {ok(p_lt_sma)} Precio {f2(price)}<SMA {f2(sma)} → Esperar")
        return "hold", mot

    # posición abierta (long)
    exit_cond = rsi_gt70 or p_lt_sma
    if exit_cond:
        mot = (f"{ok(rsi_gt70)} RSI {f2(rsi)}>70 o "
               f"{ok(p_lt_sma)} Precio {f2(price)}<SMA {f2(sma)} → Cerrar")
        return "sell", mot
    mot = (f"{ok(rsi_gt70)} RSI {f2(rsi)}>70 y "
           f"{ok(p_lt_sma)} Precio {f2(price)}<SMA {f2(sma)} → Mantener")
    return "hold", mot

# ─────────────────── EJECUCIÓN DE TRADE ─────────────────── #
def execute(pair, decision, price, rsi, sma, motivo):
    """Actualiza estado y CSVs.  Se llama desde trading_loop."""
    global capital_free, benefit_total
    st = state[pair]
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # --- comprar ---
    if decision == "buy" and not st["position"] and capital_free > 0:
        eur_to_use = capital_free * 0.999  # Usa casi todo el saldo disponible para evitar errores por fee
        if eur_to_use < 1e-6:
            return
        try:
            if not USE_TESTNET:
                order = binance.create_order(
                    pair, "market", "buy", None, None,
                    {"quoteOrderQty": eur_to_use}
                )
                st["amount"] = order["filled"] or order["amount"]  # La cantidad real comprada
                st["locked"] = order["cost"]  # Lo que realmente se gastó
            else:
                st["amount"] = eur_to_use / price
                st["locked"] = eur_to_use
            capital_free -= st["locked"]
            st.update(position=True, entry=price)
        except Exception as e:
            print("Buy error", pair, e)
            return

        append_csv(TRADES_CSV, [now, pair, f"{price:.8f}", f"{rsi:.2f}", f"{sma:.2f}", "buy"])

    # --- vender / cerrar ---
    elif decision == "sell" and st["position"]:
        try:
            if not USE_TESTNET:
                # Obtener balance real de ADA antes de vender
                base_coin = pair.split("/")[0]
                balance_real = binance.fetch_balance()["free"].get(base_coin, 0)
                amount_to_sell = min(st["amount"], balance_real)
                if amount_to_sell < 1e-6:
                    print(f"No hay saldo suficiente real de {base_coin} para vender.")
                    return
                order = binance.create_order(pair, "market", "sell", amount_to_sell)
                proceeds = order["cost"]  # Lo recibido en EUR
            else:
                proceeds = st["amount"] * price
            profit = proceeds - st["locked"]
            capital_free += proceeds
            benefit_total += profit
        except Exception as e:
            print("Sell error", pair, e)
            return

    st.update(position=False, amount=0.0, entry=0.0, locked=0.0, unreal=0.0)
    append_csv(TRADES_CSV, [now, pair, f"{price:.8f}", f"{rsi:.2f}", f"{sma:.2f}", "sell"])


    # P/L no realizado
    if st["position"]:
        st["unreal"] = (price - st["entry"]) * st["amount"]

    # Siempre registramos la evaluación
    append_csv(LOG_CSV, [now, pair, f"{price:.8f}", f"{rsi:.2f}", f"{sma:.2f}", decision, motivo])
    

# ─────────────────── CALLBACK WEB ─────────────────── #
_gui_callback = lambda: None  # no-op por defecto

def schedule_callback(func):
    """Permite a la app web registrar un callback para refrescar GUI."""
    global _gui_callback
    _gui_callback = func

# ─────────────────── TRADING LOOP ────────────────── #
def _worker():
    while True:
        try:
            for pair in TICKERS:
                df = fetch_df(pair)
                rsi, sma  = df["RSI"].iloc[-1], df["SMA"].iloc[-1]
                price     = df["close"].iloc[-1]
                dec, mot  = friendly_eval(rsi, sma, price, state[pair]["position"])
                execute(pair, dec, price, rsi, sma, mot)
            _gui_callback()          # notifica al front-end
        except Exception as exc:
            print("Loop error:", exc)
        time.sleep(60)

#threading.Thread(target=_worker, daemon=True).start()
