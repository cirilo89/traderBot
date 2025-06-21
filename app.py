import os, csv, bcrypt, time                 # ← añadido time
from flask import Flask, render_template, redirect, request, url_for, flash, jsonify
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user
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
    return jsonify(bot.state)

def tail_csv(path, n=1000):
    import collections
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(collections.deque(reader, maxlen=n))

@app.route("/api/logs")
@login_required
def api_logs():
    return jsonify(tail_csv("log_evaluaciones.csv", n=1000))

@app.route("/api/history")
@login_required
def api_history():
    return jsonify(tail_csv("historial_trades.csv", n=500))

    
# --- vaciar log_evaluaciones.csv ----------------------------------
@app.route("/api/clear_logs", methods=["POST"])
@login_required
def api_clear_logs():
    path = "log_evaluaciones.csv"

    # Sobrescribe el fichero con solo la cabecera
    with open(path, "w", newline="", encoding="utf-8") as f:
        import csv
        csv.writer(f).writerow(bot.HEAD_LOG)          # usa encabezado del bot

    # Limpia también el registro en memoria (opcional)
    if hasattr(bot, "log_records"):
        bot.log_records.clear()

    return jsonify({"ok": True})


# ---------- Callback del bot ----------
def notify_clients():
    # Aquí podrías emitir un evento WebSocket o usar Server-Sent Events
    pass

bot.schedule_callback(notify_clients)

# ---------- main ----------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=False)
