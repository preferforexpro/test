"""
mt5_manager.py
──────────────
Thread-safe MetaTrader 5 connection manager.
Handles: initialisation, auto-reconnect, rate fetching, live tick streaming.
"""

import time
import threading
import logging
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("mt5_manager")

# ── Try importing MT5; fall back to simulator if not available ──
try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False
    logger.warning("MetaTrader5 package not found – running in SIMULATION mode.")

# ── MT5 timeframe map ──
TF_MAP = {
    "1m":  5,    # TIMEFRAME_M1
    "5m":  6,    # TIMEFRAME_M5
    "15m": 7,    # TIMEFRAME_M15
    "30m": 8,    # TIMEFRAME_M30
    "1h":  16,   # TIMEFRAME_H1
    "4h":  18,   # TIMEFRAME_H4
    "1d":  24,   # TIMEFRAME_D1
    "1w":  32,   # TIMEFRAME_W1
}

# Attempt to use real enum values if mt5 is available
if MT5_AVAILABLE:
    TF_MAP = {
        "1m":  mt5.TIMEFRAME_M1,
        "5m":  mt5.TIMEFRAME_M5,
        "15m": mt5.TIMEFRAME_M15,
        "30m": mt5.TIMEFRAME_M30,
        "1h":  mt5.TIMEFRAME_H1,
        "4h":  mt5.TIMEFRAME_H4,
        "1d":  mt5.TIMEFRAME_D1,
        "1w":  mt5.TIMEFRAME_W1,
    }


class MT5Manager:
    """
    Manages the MT5 connection lifecycle and exposes clean data methods.
    All public methods are thread-safe.
    """

    def __init__(self, login: int = 0, password: str = "", server: str = "",
                 path: str = "", reconnect_interval: int = 30):
        self.login    = login
        self.password = password
        self.server   = server
        self.path     = path
        self.reconnect_interval = reconnect_interval

        self._lock       = threading.Lock()
        self._connected  = False
        self._sim_mode   = not MT5_AVAILABLE
        self._sim_engine: Optional[SimEngine] = None

        # Connect or fall back
        if self._sim_mode:
            self._init_sim()
        else:
            self._connect()
            self._start_watchdog()

    # ─────────────────────────────────────────────────────────────────
    #  CONNECTION
    # ─────────────────────────────────────────────────────────────────
    def _connect(self) -> bool:
        with self._lock:
            try:
                kwargs = {}
                if self.path:     kwargs["path"]     = self.path
                if self.login:    kwargs["login"]    = self.login
                if self.password: kwargs["password"] = self.password
                if self.server:   kwargs["server"]   = self.server

                if not mt5.initialize(**kwargs):
                    err = mt5.last_error()
                    logger.error(f"MT5 init failed: {err}")
                    self._connected = False
                    return False

                info = mt5.terminal_info()
                logger.info(f"MT5 connected: {info.company} | {info.name}")
                self._connected = True
                return True
            except Exception as e:
                logger.error(f"MT5 connection error: {e}")
                self._connected = False
                return False

    def _start_watchdog(self):
        """Background thread that reconnects on disconnect."""
        def watch():
            while True:
                time.sleep(self.reconnect_interval)
                if not self._connected:
                    logger.info("Watchdog: attempting reconnect…")
                    self._connect()
        t = threading.Thread(target=watch, daemon=True)
        t.start()

    def _init_sim(self):
        logger.info("Starting built-in simulation engine (47 forex pairs).")
        self._sim_engine = SimEngine()
        self._connected  = True   # sim is always "connected"

    # ─────────────────────────────────────────────────────────────────
    #  PUBLIC API
    # ─────────────────────────────────────────────────────────────────
    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def mode(self) -> str:
        return "simulation" if self._sim_mode else "live"

    def get_candles(self, symbol: str, timeframe: str, count: int = 150) -> Optional[pd.DataFrame]:
        """
        Returns a DataFrame with columns [time, open, high, low, close, volume].
        time is a UTC-aware datetime.
        Returns None on failure.
        """
        if self._sim_mode:
            return self._sim_engine.get_candles(symbol, timeframe, count)

        tf = TF_MAP.get(timeframe)
        if tf is None:
            raise ValueError(f"Unknown timeframe: {timeframe}")

        with self._lock:
            if not self._connected:
                return None
            rates = mt5.copy_rates_from_pos(symbol, tf, 0, count)

        if rates is None or len(rates) == 0:
            logger.warning(f"No data for {symbol} {timeframe}: {mt5.last_error()}")
            return None

        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        df.rename(columns={"tick_volume": "volume"}, inplace=True)
        return df[["time", "open", "high", "low", "close", "volume"]].copy()

    def get_tick(self, symbol: str) -> Optional[dict]:
        """Returns the latest tick as {time, bid, ask, last}."""
        if self._sim_mode:
            return self._sim_engine.get_tick(symbol)

        with self._lock:
            if not self._connected:
                return None
            tick = mt5.symbol_info_tick(symbol)

        if tick is None:
            return None
        return {
            "time":   datetime.fromtimestamp(tick.time, tz=timezone.utc).isoformat(),
            "bid":    tick.bid,
            "ask":    tick.ask,
            "last":   (tick.bid + tick.ask) / 2,
            "spread": round((tick.ask - tick.bid) * 1e5, 1),
        }

    def get_symbols(self) -> list[str]:
        if self._sim_mode:
            return self._sim_engine.symbols

        with self._lock:
            syms = mt5.symbols_get()
        return [s.name for s in syms] if syms else []

    def terminal_info(self) -> dict:
        if self._sim_mode:
            return {"mode": "simulation", "connected": True}
        with self._lock:
            info = mt5.terminal_info()
            acct  = mt5.account_info()
        return {
            "mode":     "live",
            "company":  info.company  if info else "—",
            "terminal": info.name     if info else "—",
            "broker":   acct.company  if acct else "—",
            "server":   acct.server   if acct else "—",
            "connected": self._connected,
        }

    def shutdown(self):
        if MT5_AVAILABLE and not self._sim_mode:
            with self._lock:
                mt5.shutdown()
                self._connected = False
            logger.info("MT5 shutdown.")


# ─────────────────────────────────────────────────────────────────────────────
#  SIMULATION ENGINE  –  realistic GBM candle generator (no MT5 needed)
# ─────────────────────────────────────────────────────────────────────────────
FOREX_PAIRS = {
    # symbol: (base_price, daily_vol)
    "EURUSD": (1.0850, 0.0060), "GBPUSD": (1.2730, 0.0090),
    "USDJPY": (149.20, 0.7500), "USDCHF": (0.8980, 0.0055),
    "AUDUSD": (0.6390, 0.0065), "NZDUSD": (0.5940, 0.0070),
    "USDCAD": (1.3620, 0.0060), "EURGBP": (0.8550, 0.0045),
    "EURJPY": (161.80, 0.8000), "EURCHF": (0.9720, 0.0050),
    "EURAUD": (1.6980, 0.0100), "EURNZD": (1.8250, 0.0110),
    "EURCAD": (1.4780, 0.0085), "GBPJPY": (189.60, 0.0110),
    "GBPCHF": (1.1410, 0.0080), "GBPAUD": (1.9920, 0.0120),
    "GBPNZD": (2.1420, 0.0130), "GBPCAD": (1.7330, 0.0100),
    "AUDJPY": (95.30,  0.0500), "AUDCHF": (0.5750, 0.0060),
    "AUDNZD": (1.0750, 0.0065), "AUDCAD": (0.8700, 0.0070),
    "NZDJPY": (88.60,  0.0500), "NZDCHF": (0.5350, 0.0065),
    "NZDCAD": (0.8090, 0.0080), "CHFJPY": (166.20, 0.0800),
    "CADJPY": (109.50, 0.0600), "CADCHF": (0.6590, 0.0055),
    "USDMXN": (17.15,  0.1500), "USDZAR": (18.60,  0.1800),
    "USDTRY": (32.40,  0.2500), "USDSGD": (1.3440, 0.0045),
    "USDHKD": (7.8200, 0.0020), "USDNOK": (10.68,  0.0800),
    "USDSEK": (10.48,  0.0800), "USDDKK": (6.910,  0.0045),
    "USDPLN": (3.960,  0.0300), "USDHUF": (361.0,  1.5000),
    "USDCZK": (22.85,  0.1200), "EURTRY": (35.10,  0.3000),
    "USDINR": (83.60,  0.1500), "USDTHB": (35.50,  0.0800),
    "USDMYR": (4.710,  0.0150), "USDIDR": (15820., 50.00),
    "USDPHP": (56.50,  0.1500), "USDCNH": (7.250,  0.0120),
}

TF_SECONDS = {"1m":60,"5m":300,"15m":900,"30m":1800,"1h":3600,"4h":14400,"1d":86400,"1w":604800}

class _SymSim:
    """Single-symbol GBM simulator."""
    rng = np.random.default_rng()

    def __init__(self, symbol: str, base: float, dvol: float):
        self.symbol = symbol
        self.price  = base
        self.dvol   = dvol                # daily σ
        self.trend  = 0.0
        self.trend_bars = 0
        self._candles: list[dict] = []
        self._build_history(200)

    def _bar_vol(self, tf: str) -> float:
        secs = TF_SECONDS.get(tf, 3600)
        return self.dvol * (secs / 86400) ** 0.5

    def _build_history(self, n: int, tf: str = "1h"):
        now = int(time.time())
        secs = TF_SECONDS.get(tf, 3600)
        bvol = self._bar_vol(tf)
        for i in range(n):
            self._update_trend()
            drift = self.trend * bvol * 0.2
            move  = drift + float(self.rng.normal(0, bvol))
            o = self.price
            c = max(o * 0.0001, o * (1 + move))
            rng = abs(move) + float(self.rng.exponential(bvol * 0.5))
            h = max(o, c) * (1 + abs(float(self.rng.exponential(rng * 0.4))))
            l = min(o, c) * (1 - abs(float(self.rng.exponential(rng * 0.4))))
            self._candles.append({
                "time":   datetime.fromtimestamp(now - (n - i) * secs, tz=timezone.utc).isoformat(),
                "open":   round(o, 6), "high": round(h, 6),
                "low":    round(l, 6), "close": round(c, 6),
                "volume": int(self.rng.integers(50_000, 2_000_000)),
            })
            self.price = c

    def _update_trend(self):
        if self.trend_bars <= 0:
            r = float(self.rng.random())
            self.trend = 1.0 if r < 0.35 else (-1.0 if r < 0.7 else 0.0)
            self.trend_bars = int(self.rng.integers(8, 40))
        self.trend_bars -= 1

    def tick(self, tf: str = "1h") -> dict:
        """Advance price by one micro-step; close current bar if time elapsed."""
        self._update_trend()
        bvol  = self._bar_vol(tf)
        micro = self.trend * bvol * 0.02 + float(self.rng.normal(0, bvol * 0.15))
        self.price = max(self.price * 0.0001, self.price * (1 + micro))
        return {
            "symbol": self.symbol,
            "bid":    round(self.price * 0.9999, 6),
            "ask":    round(self.price * 1.0001, 6),
            "last":   round(self.price, 6),
            "time":   datetime.now(tz=timezone.utc).isoformat(),
        }

    def get_candles(self, tf: str, count: int) -> list[dict]:
        # Build candles for this specific TF on demand
        secs = TF_SECONDS.get(tf, 3600)
        now  = int(time.time())
        bvol = self._bar_vol(tf)
        price = self.price
        candles = []
        for i in range(count, 0, -1):
            self._update_trend()
            drift = self.trend * bvol * 0.2
            move  = drift + float(self.rng.normal(0, bvol))
            o = price
            c = max(o * 0.0001, o * (1 + move))
            rng_ = abs(move) + float(self.rng.exponential(bvol * 0.5))
            h = max(o, c) * (1 + abs(float(self.rng.exponential(rng_ * 0.4))))
            l = min(o, c) * (1 - abs(float(self.rng.exponential(rng_ * 0.4))))
            candles.append({
                "time":   datetime.fromtimestamp(now - i * secs, tz=timezone.utc).isoformat(),
                "open":   round(o, 6), "high": round(h, 6),
                "low":    round(l, 6), "close": round(c, 6),
                "volume": int(self.rng.integers(50_000, 2_000_000)),
            })
            price = c
        return candles


class SimEngine:
    def __init__(self):
        self._sims: dict[str, _SymSim] = {
            sym: _SymSim(sym, bp, dv) for sym, (bp, dv) in FOREX_PAIRS.items()
        }

    @property
    def symbols(self) -> list[str]:
        return list(self._sims.keys())

    def get_candles(self, symbol: str, timeframe: str, count: int) -> Optional[pd.DataFrame]:
        sym = symbol.replace("=X", "").upper()
        sim = self._sims.get(sym)
        if sim is None:
            return None
        rows = sim.get_candles(timeframe, count)
        df   = pd.DataFrame(rows)
        df["time"] = pd.to_datetime(df["time"], utc=True)
        return df

    def get_tick(self, symbol: str) -> Optional[dict]:
        sym = symbol.replace("=X", "").upper()
        sim = self._sims.get(sym)
        return sim.tick() if sim else None

    def tick_all(self) -> list[dict]:
        return [s.tick() for s in self._sims.values()]
