import os, csv, sqlite3, bcrypt
from flask import Flask, render_template, redirect, request, url_for, flash, jsonify
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import bot
from dotenv import load_dotenv
load_dotenv()
# --- Configuración ---
WEB_USER = os.getenv("WEB_USER", "admin")
WEB_PASS_HASH = os.getenv("WEB_PASS_HASH")            # hash bcrypt en .env
# Si prefieres prototipo rápido: WEB_PASS = os.getenv("WEB_PASS", "changeme")

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET", "change-me")

login_manager = LoginManager(app)
login_manager.login_view = "login"

# Cabecera usada si se desea limpiar historial_trades.csv
HEAD_TRADE = ["datetime", "pair", "Close", "RSI", "SMA", "decision"]

class User(UserMixin):
    id = 1  # single-user


# --- nuevo endpoint ---
@app.route("/api/balance")
@login_required
def api_balance():
    # accedemos a capital_free y benefit_total que mantiene bot.py
    return jsonify({
        "free": bot.capital_free,
        "benefit": bot.benefit_total
    })


@login_manager.user_loader
def load_user(user_id):
    return User() if user_id == "1" else None

# ---------- Rutas ----------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = request.form["username"]
        pwd  = request.form["password"].encode()
        if user == WEB_USER:
            if WEB_PASS_HASH and bcrypt.checkpw(pwd, WEB_PASS_HASH.encode()):
                login_user(User()); return redirect(url_for("dashboard"))
            # modo demo (NO para producción) si no hay hash
            elif not WEB_PASS_HASH:
                login_user(User()); return redirect(url_for("dashboard"))
        flash("Credenciales inválidas", "danger")
    return render_template("login.html")

@app.route("/logout")
@login_required
def logout():
    logout_user(); return redirect(url_for("login"))

@app.route("/")
@login_required
def dashboard():
    return render_template("dashboard.html")

# ---------- Endpoints AJAX ----------
@app.route("/api/state")
@login_required
def api_state():
    balances = bot.binance.fetch_balance()["free"]  # obten saldos reales
    state_real = {}
    for pair in bot.TICKERS:
        base, quote = pair.split("/")
        state_real[pair] = {
            "position": balances.get(base, 0) > 0,
            "amount": balances.get(base, 0),
            "entry": bot.state[pair].get("entry", 0.0),   # puedes dejarlo como está, o poner 0
            "locked": 0.0,
            "unreal": 0.0
        }
    return jsonify(state_real)


def tail_db(n=1000):
    conn = sqlite3.connect(bot.LOG_DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM log_evaluaciones ORDER BY rowid DESC LIMIT ?",
        (n,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in reversed(rows)]

@app.route("/api/logs")
@login_required
def api_logs():
    return jsonify(tail_db(n=1000))

@app.route("/api/history")
@login_required
def api_history():
    # Función helper que atrapa errores para cada par
    def fetch_trades(pair):
        try:
            # ccxt: fetch_my_trades devuelve la lista de trades ejecutados
            return bot.binance.fetch_my_trades(pair)
        except Exception as e:
            app.logger.error(f"Error fetching trades for {pair}: {e}")
            return []

    # Lanza una petición por cada ticker en paralelo
    with ThreadPoolExecutor(max_workers=len(bot.TICKERS)) as executor:
        all_results = executor.map(fetch_trades, bot.TICKERS)

    # Aplana la lista y formatea cada trade
    trades = []
    for trades_list in all_results:
        for t in trades_list:
            trades.append({
                "datetime": datetime.fromtimestamp(t['timestamp'] / 1000).isoformat(),
                "pair":     t['symbol'],
                "price":    float(t['price']),
                "amount":   float(t['amount']),
                "side":     t['side']
            })

    # Ordena por fecha descendente y limita (p.ej.) a los últimos 500
    trades.sort(key=lambda x: x['datetime'], reverse=True)
    return jsonify(trades[:500])
@app.route("/api/clear_logs", methods=["POST"])
@login_required
def api_clear_logs():
    conn = sqlite3.connect(bot.LOG_DB)
    conn.execute("DELETE FROM log_evaluaciones")
    conn.commit()
    conn.close()

    if hasattr(bot, "log_records"):
        bot.log_records.clear()

    return jsonify({"ok": True})

@app.route("/api/clear_history", methods=["POST"])
@login_required
def api_clear_history():
    path = "historial_trades.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        import csv
        csv.writer(f).writerow(HEAD_TRADE)
    # Opcional: limpiar estructura en memoria, si la usas
    return jsonify({"ok": True})

# ---------- Callback del bot ----------
def notify_clients():
    # Aquí podrías emitir un evento WebSocket o usar Server-Sent Events
    pass

bot.schedule_callback(notify_clients)

# ---------- main ----------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=False)
