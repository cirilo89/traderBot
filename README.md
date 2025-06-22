# traderBot

Pequeño bot de trading con interfaz web basada en Flask. Registra las evaluaciones de la estrategia en una base de datos SQLite (`logs.db`) y permite consultar el estado de la cuenta y el historial de operaciones.

## Requisitos
- Python 3.11+
- Dependencias listadas en `requirements.txt`
- Cuenta de Binance con API habilitada

## Configuración
1. Crea un archivo `.env` con tus claves y parámetros:
   ```
   BINANCE_API_KEY=TU_API_KEY
   BINANCE_API_SECRET=TU_API_SECRET
   BASE_CURRENCY=EUR
   TICKERS=BTC/EUR,ETH/EUR,ADA/EUR
   RSI_PERIOD=14
   SMA_PERIOD=20
   FLASK_SECRET=una-clave-secreta
   WEB_USER=admin
   WEB_PASS_HASH=<hash bcrypt de la contraseña>
   ```
2. Instala las dependencias:
   ```bash
   pip install -r requirements.txt
   ```

## Uso
Ejecuta la aplicación con:
```bash
python app.py
```
Accede a `http://localhost:8000` e inicia sesión con las credenciales configuradas.

Para producción se recomienda usar el `Dockerfile` incluido o un servidor WSGI como Gunicorn.
