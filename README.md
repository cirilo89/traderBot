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
   TICKERS=BTC/EUR,ETH/EUR,ADA/EUR,BNB/EUR,SOL/EUR,DOT/EUR
   RSI_PERIOD=14
   SMA_PERIOD=20
   TRADE_FRACTION=0.33
   RSI_LOW=30
   RSI_HIGH=70
   TAKE_PROFIT_PCT=0.02
   FLASK_SECRET=una-clave-secreta
   WEB_USER=admin
   WEB_PASS_HASH=<hash bcrypt de la contraseña>
   # Tamaño máximo de logs.db en MB (opcional)
   LOG_DB_MAX_MB=5
   ```
   - `TRADE_FRACTION` indica la fracción del capital libre que se usará en cada operación.
   - `RSI_LOW` y `RSI_HIGH` permiten ajustar los umbrales de sobreventa y sobrecompra.
   - `TAKE_PROFIT_PCT` define un objetivo de beneficio (por ejemplo `0.02` equivale a un 2 %).
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

El panel ahora incluye una gráfica con el beneficio diario que se alimenta de
la ruta `/api/profit_series`.

Para producción se recomienda usar el `Dockerfile` incluido o un servidor WSGI como Gunicorn.

La ruta `/api/balance` calcula el beneficio total a partir del historial de operaciones
obtenido de Binance. Así, el valor reflejado en el panel se mantiene actualizado
incluso tras reiniciar la aplicación.
