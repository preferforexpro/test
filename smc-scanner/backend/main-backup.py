"""
main.py
───────
FastAPI backend for the SMC Forex Scanner.

Endpoints:
  GET  /                      – health check + server info
  GET  /api/info              – terminal info, data source, pairs count
  GET  /api/pairs             – list of all supported forex pairs
  GET  /api/candles/{symbol}  – OHLCV candles  ?tf=1h&count=150
  GET  /api/tick/{symbol}     – latest tick (bid/ask/last)
  GET  /api/analyse/{symbol}  – full SMC analysis for one pair ?tf=1h
  GET  /api/scan              – SMC analysis for all pairs (batch) ?tf=1h
  WS   /ws/ticks              – live tick stream (all pairs, ~2 s interval)
  WS   /ws/scan               – live SMC scan stream (rolling, all pairs)

Run:
  uvicorn main:app --host 0.0.0.0 --port 8000 --reload
"""

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from mt5_manager import MT5Manager, FOREX_PAIRS
from smc_engine import analyse

# ─────────────────────────────────────────────────────────────────────────────
#  LOGGING
# ─────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("smc_server")

# ─────────────────────────────────────────────────────────────────────────────
#  MT5 CONFIGURATION  (override via env vars or .env)
# ─────────────────────────────────────────────────────────────────────────────
MT5_LOGIN    = int(os.getenv("MT5_LOGIN",    "0"))
MT5_PASSWORD = os.getenv("MT5_PASSWORD",     "")
MT5_SERVER   = os.getenv("MT5_SERVER",       "")
MT5_PATH     = os.getenv("MT5_PATH",         "")   # e.g. C:\Program Files\MetaTrader 5\terminal64.exe

# ─────────────────────────────────────────────────────────────────────────────
#  GLOBAL STATE
# ─────────────────────────────────────────────────────────────────────────────
mt5: MT5Manager = None   # type: ignore

# WebSocket connection pools
tick_clients: set[WebSocket] = set()
scan_clients: set[WebSocket] = set()

# In-memory scan cache  {symbol → analysis_dict}
scan_cache: dict[str, dict] = {}

ALL_SYMBOLS = list(FOREX_PAIRS.keys())

# ─────────────────────────────────────────────────────────────────────────────
#  LIFESPAN  (startup / shutdown)
# ─────────────────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    global mt5
    logger.info("Starting MT5 Manager…")
    mt5 = MT5Manager(
        login=MT5_LOGIN,
        password=MT5_PASSWORD,
        server=MT5_SERVER,
        path=MT5_PATH,
    )
    logger.info(f"Data source: {mt5.mode}")

    # Background tasks
    asyncio.create_task(_tick_broadcaster())
    asyncio.create_task(_scan_runner())

    yield

    logger.info("Shutting down MT5…")
    mt5.shutdown()


# ─────────────────────────────────────────────────────────────────────────────
#  APP
# ─────────────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="SMC Forex Scanner API",
    version="3.0.0",
    description="MetaTrader 5 + Smart Money Concepts real-time backend",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],    # tighten in production
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─────────────────────────────────────────────────────────────────────────────
#  REST ENDPOINTS
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/")
async def root():
    return {
        "service": "SMC Forex Scanner API",
        "version": "3.0.0",
        "status":  "running",
        "mode":    mt5.mode,
        "pairs":   len(ALL_SYMBOLS),
        "docs":    "/docs",
    }


@app.get("/api/info")
async def info():
    return {
        **mt5.terminal_info(),
        "pairs":  len(ALL_SYMBOLS),
        "cached": len(scan_cache),
        "ws_tick_clients": len(tick_clients),
        "ws_scan_clients": len(scan_clients),
    }


@app.get("/api/pairs")
async def pairs():
    return {"pairs": ALL_SYMBOLS, "count": len(ALL_SYMBOLS)}


@app.get("/api/candles/{symbol}")
async def candles(
    symbol: str,
    tf:     str = Query("1h",  description="Timeframe: 1m 5m 15m 30m 1h 4h 1d"),
    count:  int = Query(150,   ge=20, le=500, description="Number of bars"),
):
    sym = symbol.upper()
    df  = mt5.get_candles(sym, tf, count)
    if df is None:
        raise HTTPException(404, detail=f"No data for {sym} {tf}")
    return {
        "symbol":    sym,
        "timeframe": tf,
        "count":     len(df),
        "candles":   df.to_dict(orient="records"),
    }


@app.get("/api/tick/{symbol}")
async def tick(symbol: str):
    sym  = symbol.upper()
    data = mt5.get_tick(sym)
    if data is None:
        raise HTTPException(404, detail=f"No tick for {sym}")
    return data


@app.get("/api/analyse/{symbol}")
async def analyse_symbol(
    symbol: str,
    tf:     str = Query("1h"),
    count:  int = Query(150, ge=30, le=500),
):
    sym = symbol.upper()
    df  = mt5.get_candles(sym, tf, count)
    if df is None:
        raise HTTPException(404, detail=f"No data for {sym} {tf}")
    result = analyse(df, symbol=sym, timeframe=tf)
    if result is None:
        raise HTTPException(422, detail=f"Insufficient data for SMC analysis of {sym}")
    return result


@app.get("/api/scan")
async def scan_all(
    tf:    str = Query("1h"),
    count: int = Query(150, ge=30, le=500),
):
    """Full SMC scan of all pairs. Can be slow (47 MT5 calls) – prefer WS /ws/scan."""
    results = {}
    for sym in ALL_SYMBOLS:
        df = mt5.get_candles(sym, tf, count)
        r  = analyse(df, symbol=sym, timeframe=tf)
        if r:
            results[sym] = r
    return {
        "timeframe": tf,
        "count":     len(results),
        "results":   results,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  WEBSOCKET — TICK STREAM
# ─────────────────────────────────────────────────────────────────────────────
@app.websocket("/ws/ticks")
async def ws_ticks(ws: WebSocket):
    """
    Streams tick updates for all pairs every ~2 seconds.
    Message format: {"type": "ticks", "data": [{symbol, bid, ask, last, time}, …]}
    """
    await ws.accept()
    tick_clients.add(ws)
    logger.info(f"Tick WS connected. Total clients: {len(tick_clients)}")
    try:
        while True:
            await ws.receive_text()   # keep-alive / client ping
    except WebSocketDisconnect:
        pass
    finally:
        tick_clients.discard(ws)
        logger.info(f"Tick WS disconnected. Total clients: {len(tick_clients)}")


# ─────────────────────────────────────────────────────────────────────────────
#  WEBSOCKET — LIVE SMC SCAN STREAM
# ─────────────────────────────────────────────────────────────────────────────
@app.websocket("/ws/scan")
async def ws_scan(ws: WebSocket):
    """
    On connect: immediately sends the full cached scan result.
    Then streams incremental updates as pairs are re-analysed.

    Client → server:  {"tf": "4h"}            change timeframe
    Server → client:  {"type":"init",  "data": {sym: analysis, …}}   (on connect)
                      {"type":"update", "data": {sym: analysis}}      (incremental)
                      {"type":"alert",  "data": {signal details}}     (signal change)
                      {"type":"ping",   "ts": unix_ms}                (heartbeat)
    """
    await ws.accept()
    scan_clients.add(ws)
    logger.info(f"Scan WS connected. Total clients: {len(scan_clients)}")

    # Send full cache immediately
    if scan_cache:
        await ws.send_json({"type": "init", "data": scan_cache, "mode": mt5.mode})

    try:
        while True:
            # Listen for client messages (e.g. TF change)
            raw = await asyncio.wait_for(ws.receive_text(), timeout=30.0)
            try:
                msg = json.loads(raw)
                if msg.get("type") == "ping":
                    await ws.send_json({"type": "pong", "ts": int(time.time() * 1000)})
                # Future: handle TF change requests here
            except json.JSONDecodeError:
                pass
    except (WebSocketDisconnect, asyncio.TimeoutError):
        pass
    finally:
        scan_clients.discard(ws)
        logger.info(f"Scan WS disconnected. Total clients: {len(scan_clients)}")


# ─────────────────────────────────────────────────────────────────────────────
#  BACKGROUND TASKS
# ─────────────────────────────────────────────────────────────────────────────
async def _broadcast(clients: set[WebSocket], payload: dict):
    dead = set()
    for ws in list(clients):
        try:
            await ws.send_json(payload)
        except Exception:
            dead.add(ws)
    clients -= dead


async def _tick_broadcaster():
    """Fetch and broadcast ticks for all pairs every 2 seconds."""
    logger.info("Tick broadcaster started.")
    while True:
        await asyncio.sleep(2)
        if not tick_clients:
            continue
        try:
            loop = asyncio.get_event_loop()
            ticks = await loop.run_in_executor(
                None,
                lambda: [mt5.get_tick(s) for s in ALL_SYMBOLS]
            )
            ticks = [t for t in ticks if t]
            if ticks:
                await _broadcast(tick_clients, {"type": "ticks", "data": ticks})
        except Exception as e:
            logger.error(f"Tick broadcast error: {e}")


async def _scan_runner():
    """
    Continuously re-analyses pairs in a rolling window.
    • Cycles through all 47 pairs
    • Processes 3 pairs per 4-second tick  → full cycle ≈ 60 s
    • Publishes incremental updates to scan WS clients
    • Detects signal changes → fires alerts
    """
    logger.info("Scan runner started.")
    TF     = "1h"
    COUNT  = 150
    idx    = 0
    BATCH  = 3
    SLEEP  = 4          # seconds between batches
    prev   = {}         # previous analysis per symbol

    # Initial full scan (staggered so we don't hammer MT5 on startup)
    for sym in ALL_SYMBOLS:
        await asyncio.sleep(0.8)
        df = await asyncio.get_event_loop().run_in_executor(
            None, lambda s=sym: mt5.get_candles(s, TF, COUNT)
        )
        r = analyse(df, symbol=sym, timeframe=TF)
        if r:
            scan_cache[sym] = r
            prev[sym] = r

    logger.info(f"Initial scan complete: {len(scan_cache)}/{len(ALL_SYMBOLS)} pairs.")

    # Broadcast initial cache to any early WS clients
    if scan_cache and scan_clients:
        await _broadcast(scan_clients, {"type": "init", "data": scan_cache, "mode": mt5.mode})

    # Rolling update loop
    while True:
        await asyncio.sleep(SLEEP)
        batch = [ALL_SYMBOLS[(idx + k) % len(ALL_SYMBOLS)] for k in range(BATCH)]
        idx   = (idx + BATCH) % len(ALL_SYMBOLS)

        for sym in batch:
            try:
                df = await asyncio.get_event_loop().run_in_executor(
                    None, lambda s=sym: mt5.get_candles(s, TF, COUNT)
                )
                r = analyse(df, symbol=sym, timeframe=TF)
                if r is None:
                    continue

                scan_cache[sym] = r
                old = prev.get(sym)

                # Broadcast update
                if scan_clients:
                    await _broadcast(scan_clients, {
                        "type":   "update",
                        "symbol": sym,
                        "data":   r,
                    })

                # Alert detection
                if old and scan_clients:
                    alerts = _detect_alerts(sym, old, r, TF)
                    for alert in alerts:
                        await _broadcast(scan_clients, {"type": "alert", "data": alert})

                prev[sym] = r

            except Exception as e:
                logger.error(f"Scan error for {sym}: {e}")

        # Heartbeat
        if scan_clients:
            await _broadcast(scan_clients, {
                "type": "ping",
                "ts":   int(time.time() * 1000),
                "cached": len(scan_cache),
            })


def _detect_alerts(symbol: str, old: dict, new: dict, tf: str) -> list[dict]:
    alerts = []
    base = {"symbol": symbol, "timeframe": tf, "price": new["price"], "ts": int(time.time() * 1000)}

    # CHoCH appeared
    if new.get("choch") is not None and old.get("choch") is None:
        alerts.append({**base, "signal": "CHoCH", "direction": new["choch"],
                       "level": new.get("choch_level")})

    # BOS appeared
    elif new.get("bos") is not None and old.get("bos") is None:
        alerts.append({**base, "signal": "BOS", "direction": new["bos"],
                       "level": new.get("bos_level")})

    # New FVG
    elif new.get("fvg") and not old.get("fvg"):
        alerts.append({**base, "signal": "FVG", "direction": new["fvg"]["type"]})

    # Bias flip
    elif new.get("bias") != old.get("bias") and new.get("bias") != 0:
        alerts.append({**base, "signal": "Bias Flip", "direction": new["bias"],
                       "from_bias": old.get("bias"), "to_bias": new.get("bias")})

    return alerts
