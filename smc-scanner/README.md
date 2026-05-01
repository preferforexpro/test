# SMC Forex Scanner — MT5 + FastAPI + WebSocket

Real-time Smart Money Concepts scanner for 47 forex pairs.
Powered by MetaTrader 5 (live broker data) with automatic GBM simulation fallback.

```
┌─────────────────────────────────────────────────────────────┐
│  Browser  (frontend/index.html)                             │
│    └── WebSocket ws://localhost:8000/ws/scan                │
│    └── REST      http://localhost:8000/api/*                │
│                                                             │
│  FastAPI Backend  (backend/main.py)                         │
│    └── MT5Manager  ←→  MetaTrader5 terminal (Windows)       │
│    └── SMC Engine  (smc_engine.py)                          │
│    └── WebSocket broadcaster                                │
│    └── Auto-reconnect watchdog                              │
└─────────────────────────────────────────────────────────────┘
```

---

## Requirements

| Component       | Requirement                                     |
|-----------------|-------------------------------------------------|
| Python          | 3.11+ (Windows preferred for MT5)               |
| MetaTrader 5    | Desktop terminal installed + logged-in account  |
| Broker account  | Any MT5 broker — demo accounts work perfectly   |
| Browser         | Any modern browser for the frontend             |

> **No MT5?** The backend auto-falls back to a realistic GBM simulation.
> All 47 pairs will still have live-ticking prices and SMC signals.

---

## Quick Start

### 1. Clone / copy the project
```
smc-scanner/
├── backend/
│   ├── main.py
│   ├── mt5_manager.py
│   ├── smc_engine.py
│   ├── requirements.txt
│   └── .env.example
└── frontend/
    └── index.html
```

### 2. Install Python dependencies
```bash
cd backend
pip install -r requirements.txt
```

### 3. Configure MT5 credentials
```bash
cp .env.example .env
# Edit .env with your broker details:
```
```env
MT5_LOGIN=12345678
MT5_PASSWORD=your_password
MT5_SERVER=YourBroker-Demo
MT5_PATH=C:\Program Files\MetaTrader 5\terminal64.exe
```
> Leave all blank to use simulation mode (no MT5 needed).

### 4. Start the backend
```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```
You should see:
```
INFO  | MT5 Manager | Connected: YourBroker | Demo Account
INFO  | smc_server  | Data source: live
INFO  | smc_server  | Tick broadcaster started.
INFO  | smc_server  | Scan runner started.
```

### 5. Open the frontend
Open `frontend/index.html` directly in your browser.
The frontend auto-connects to `ws://localhost:8000/ws/scan`.

---

## API Reference

### REST Endpoints

| Method | Path                        | Description                          |
|--------|-----------------------------|--------------------------------------|
| GET    | `/`                         | Health check + server info           |
| GET    | `/api/info`                 | Terminal info, WS client count       |
| GET    | `/api/pairs`                | List all 47 supported pairs          |
| GET    | `/api/candles/{SYMBOL}`     | OHLCV data  `?tf=1h&count=150`       |
| GET    | `/api/tick/{SYMBOL}`        | Latest bid/ask/last tick             |
| GET    | `/api/analyse/{SYMBOL}`     | Full SMC analysis `?tf=1h`           |
| GET    | `/api/scan`                 | Batch SMC scan for all pairs         |
| GET    | `/docs`                     | Interactive Swagger UI               |

### WebSocket Endpoints

#### `ws://localhost:8000/ws/scan`  — Live SMC stream (main)
```
Server → Client messages:

{"type": "init",   "data": {EURUSD: {...smc}, GBPUSD: {...smc}, …}, "mode": "live"}
{"type": "update", "symbol": "EURUSD", "data": {...smc}}
{"type": "alert",  "data": {"symbol":"EURUSD","signal":"CHoCH","direction":1,"price":1.0874,"level":1.0860,"timeframe":"1h","ts":1714567890000}}
{"type": "ping",   "ts": 1714567890000, "cached": 47}

Client → Server messages:
{"type": "ping"}   → {"type": "pong", "ts": ...}
```

#### `ws://localhost:8000/ws/ticks`  — Raw tick stream
```
{"type": "ticks", "data": [{"symbol":"EURUSD","bid":1.0849,"ask":1.0851,"last":1.0850,"time":"2024-05-01T10:00:00+00:00"}, …]}
```

### SMC Analysis Object (per pair)
```json
{
  "symbol":      "EURUSD",
  "timeframe":   "1h",
  "price":       1.08502,
  "change_pct":  0.0312,
  "bid":         1.08499,
  "ask":         1.08505,
  "fvg":         {"type": 1, "top": 1.0855, "bot": 1.0848, "mitigated": false},
  "ob":          {"type": 1, "top": 1.0840, "bot": 1.0832, "mitigated": false},
  "bos":         1,
  "choch":       null,
  "bos_level":   1.0862,
  "liquidity":   {"type": -1, "label": "SSL Below", "level": 1.0820},
  "swing":       {"label": "HH / HL", "bias": 1},
  "pd_zone":     {"zone": "discount", "pct": 38.2, "top": 1.0890, "bot": 1.0810, "mid": 1.0850},
  "bias":        1,
  "score":       3.8,
  "confluence":  5,
  "spark":       [1.0821, 1.0834, …]
}
```

---

## Supported Forex Pairs (47)

**Majors (7):** EUR/USD, GBP/USD, USD/JPY, USD/CHF, AUD/USD, NZD/USD, USD/CAD

**Minors (21):** EUR/GBP, EUR/JPY, EUR/CHF, EUR/AUD, EUR/NZD, EUR/CAD, GBP/JPY, GBP/CHF,
GBP/AUD, GBP/NZD, GBP/CAD, AUD/JPY, AUD/CHF, AUD/NZD, AUD/CAD, NZD/JPY, NZD/CHF,
NZD/CAD, CHF/JPY, CAD/JPY, CAD/CHF

**Exotics (19):** USD/MXN, USD/ZAR, USD/TRY, USD/SGD, USD/HKD, USD/NOK, USD/SEK, USD/DKK,
USD/PLN, USD/HUF, USD/CZK, EUR/TRY, USD/INR, USD/THB, USD/MYR, USD/IDR, USD/PHP, USD/CNH

---

## Deployment (Production)

### Option A — Windows VPS (recommended for MT5)
```bash
# Install MT5 terminal on Windows VPS
# Run backend as a service using NSSM or Task Scheduler
uvicorn main:app --host 0.0.0.0 --port 8000
```

### Option B — Docker (simulation mode only, MT5 needs Windows)
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### Frontend — any static host
Upload `frontend/index.html` to Vercel, Netlify, or any web server.
Update `WS_URL` in the HTML to your server's public address:
```js
const WS_URL  = "wss://your-server.com/ws/scan";
const API_URL = "https://your-server.com";
```

---

## Update Intervals

| Component              | Interval              |
|------------------------|-----------------------|
| WebSocket tick stream  | Every 2 seconds       |
| SMC scan (per pair)    | Rolling, ~60s cycle   |
| Full cycle (47 pairs)  | ≈ 63 seconds          |
| Frontend flash/update  | Immediate on WS msg   |
| Ping/heartbeat         | Every 20 seconds      |

---

## SMC Indicators Explained

| Signal    | Description                                                   |
|-----------|---------------------------------------------------------------|
| **FVG**   | Fair Value Gap — 3-candle imbalance, bull or bear             |
| **OB**    | Order Block — last opposing candle before a swing             |
| **BOS**   | Break of Structure — structure continuation confirmed         |
| **CHoCH** | Change of Character — potential trend reversal                |
| **BSL**   | Buy-Side Liquidity — equal highs (resting orders above)       |
| **SSL**   | Sell-Side Liquidity — equal lows (resting orders below)       |
| **P/D**   | Premium/Discount zone (above/below 50% of swing range)        |
| **Bias**  | Composite score of all above — Bull / Bear / Neutral          |

---

## Troubleshooting

**MT5 won't connect:**
- Ensure MT5 terminal is open and logged in before starting the server
- Check `MT5_PATH` points to `terminal64.exe`
- Try running with admin privileges
- Verify `MT5_SERVER` matches exactly what's shown in MT5's File → Open Account

**WebSocket not connecting:**
- Check CORS — browser must be able to reach `ws://localhost:8000`
- For HTTPS sites, use `wss://` and put the backend behind nginx with SSL

**Missing pairs:**
- Not all pairs are available on every broker
- The server will log warnings for unavailable symbols and skip them
