"""
smc_engine.py
─────────────
Pure-Python / NumPy implementation of Smart Money Concepts (ICT).

Indicators:
  • Swing Highs / Lows
  • Fair Value Gaps (FVG) — bullish & bearish, mitigated flag
  • Order Blocks (OB)   — bullish & bearish, mitigated flag
  • Break of Structure (BOS)
  • Change of Character (CHoCH)
  • Liquidity pools (BSL / SSL)
  • Swing Structure (HH/HL, LH/LL …)
  • Composite Bias Score
"""

from __future__ import annotations
import math
from typing import Optional
import pandas as pd
import numpy as np


# ─────────────────────────────────────────────────────────────────────────────
#  SWING HIGHS / LOWS
# ─────────────────────────────────────────────────────────────────────────────
def find_swings(df: pd.DataFrame, length: int = 5) -> list[dict]:
    """
    Returns a list of swing points:
      {"index": int, "type": 1 (high) | -1 (low), "level": float}
    """
    highs = df["high"].values
    lows  = df["low"].values
    n     = len(df)
    swings = []

    for i in range(length, n - length):
        is_hi = all(highs[i] >= highs[j] for j in range(i - length, i + length + 1) if j != i)
        is_lo = all(lows[i]  <= lows[j]  for j in range(i - length, i + length + 1) if j != i)
        if is_hi:
            swings.append({"index": i, "type":  1, "level": float(highs[i])})
        elif is_lo:
            swings.append({"index": i, "type": -1, "level": float(lows[i])})

    return swings


# ─────────────────────────────────────────────────────────────────────────────
#  FAIR VALUE GAPS
# ─────────────────────────────────────────────────────────────────────────────
def find_fvg(df: pd.DataFrame) -> list[dict]:
    """
    A 3-candle imbalance:
      Bull FVG: candle[i-1].high < candle[i+1].low
      Bear FVG: candle[i-1].low  > candle[i+1].high
    """
    h = df["high"].values
    l = df["low"].values
    c = df["close"].values
    last_close = c[-1]
    fvgs = []

    for i in range(1, len(df) - 1):
        if h[i - 1] < l[i + 1]:                 # bullish
            mitigated = last_close < l[i + 1]
            fvgs.append({
                "type": 1, "index": i,
                "top": float(l[i + 1]), "bot": float(h[i - 1]),
                "mid": float((l[i + 1] + h[i - 1]) / 2),
                "mitigated": mitigated,
            })
        elif l[i - 1] > h[i + 1]:               # bearish
            mitigated = last_close > h[i + 1]
            fvgs.append({
                "type": -1, "index": i,
                "top": float(l[i - 1]), "bot": float(h[i + 1]),
                "mid": float((l[i - 1] + h[i + 1]) / 2),
                "mitigated": mitigated,
            })

    return fvgs


# ─────────────────────────────────────────────────────────────────────────────
#  ORDER BLOCKS
# ─────────────────────────────────────────────────────────────────────────────
def find_order_blocks(df: pd.DataFrame, swings: list[dict], lookback: int = 20) -> list[dict]:
    """
    Bullish OB : the last bearish candle before a bullish swing high.
    Bearish OB : the last bullish candle before a bearish swing low.
    """
    o = df["open"].values
    h = df["high"].values
    l = df["low"].values
    c = df["close"].values
    last_close = c[-1]
    obs = []

    hi_swings = [s for s in swings if s["type"] ==  1]
    lo_swings = [s for s in swings if s["type"] == -1]

    for sw in hi_swings[-5:]:
        for k in range(sw["index"] - 1, max(0, sw["index"] - lookback) - 1, -1):
            if c[k] < o[k]:   # bearish candle → bullish OB
                obs.append({
                    "type": 1, "index": k,
                    "top": float(h[k]), "bot": float(c[k]),
                    "mitigated": last_close < c[k],
                })
                break

    for sw in lo_swings[-5:]:
        for k in range(sw["index"] - 1, max(0, sw["index"] - lookback) - 1, -1):
            if c[k] > o[k]:   # bullish candle → bearish OB
                obs.append({
                    "type": -1, "index": k,
                    "top": float(c[k]), "bot": float(l[k]),
                    "mitigated": last_close > c[k],
                })
                break

    return obs


# ─────────────────────────────────────────────────────────────────────────────
#  BOS / CHoCH
# ─────────────────────────────────────────────────────────────────────────────
def find_bos_choch(df: pd.DataFrame, swings: list[dict]) -> dict:
    """
    Returns {"bos": 1|-1|None, "choch": 1|-1|None,
             "bos_level": float|None, "choch_level": float|None}
    """
    hi = [s for s in swings if s["type"] ==  1]
    lo = [s for s in swings if s["type"] == -1]

    result = {"bos": None, "choch": None, "bos_level": None, "choch_level": None}
    if len(hi) < 2 or len(lo) < 2:
        return result

    rH, pH = hi[-1], hi[-2]
    rL, pL = lo[-1], lo[-2]
    recent = df["close"].values[-15:]

    bull_structure = rH["level"] > pH["level"] and rL["level"] > pL["level"]
    bear_structure = rH["level"] < pH["level"] and rL["level"] < pL["level"]

    high_break = any(c > rH["level"] for c in recent)
    low_break  = any(c < rL["level"] for c in recent)

    if high_break:
        if bull_structure:
            result["bos"] = 1; result["bos_level"] = rH["level"]
        else:
            result["choch"] = 1; result["choch_level"] = rH["level"]
    elif low_break:
        if bear_structure:
            result["bos"] = -1; result["bos_level"] = rL["level"]
        else:
            result["choch"] = -1; result["choch_level"] = rL["level"]

    return result


# ─────────────────────────────────────────────────────────────────────────────
#  LIQUIDITY POOLS
# ─────────────────────────────────────────────────────────────────────────────
def find_liquidity(df: pd.DataFrame, swings: list[dict], tolerance_pct: float = 0.0025) -> Optional[dict]:
    """
    Equal highs → Buy-Side Liquidity (BSL).
    Equal lows  → Sell-Side Liquidity (SSL).
    """
    hi = [s for s in swings if s["type"] ==  1]
    lo = [s for s in swings if s["type"] == -1]
    last = float(df["close"].values[-1])

    bsl = ssl = None
    for i in range(len(hi) - 1):
        for j in range(i + 1, len(hi)):
            if abs(hi[i]["level"] - hi[j]["level"]) / hi[i]["level"] < tolerance_pct:
                lvl = (hi[i]["level"] + hi[j]["level"]) / 2
                bsl = {"level": lvl, "swept": last > max(hi[i]["level"], hi[j]["level"])}
                break
        if bsl:
            break

    for i in range(len(lo) - 1):
        for j in range(i + 1, len(lo)):
            if abs(lo[i]["level"] - lo[j]["level"]) / lo[i]["level"] < tolerance_pct:
                lvl = (lo[i]["level"] + lo[j]["level"]) / 2
                ssl = {"level": lvl, "swept": last < min(lo[i]["level"], lo[j]["level"])}
                break
        if ssl:
            break

    if bsl and not bsl["swept"]: return {"type":  1, "label": "BSL Above", "level": bsl["level"]}
    if ssl and not ssl["swept"]: return {"type": -1, "label": "SSL Below", "level": ssl["level"]}
    if bsl and bsl["swept"]:     return {"type": -1, "label": "BSL Swept", "level": bsl["level"]}
    if ssl and ssl["swept"]:     return {"type":  1, "label": "SSL Swept", "level": ssl["level"]}
    return None


# ─────────────────────────────────────────────────────────────────────────────
#  SWING STRUCTURE
# ─────────────────────────────────────────────────────────────────────────────
def find_swing_structure(swings: list[dict]) -> Optional[dict]:
    hi = [s for s in swings if s["type"] ==  1]
    lo = [s for s in swings if s["type"] == -1]
    if len(hi) < 2 or len(lo) < 2:
        return None

    hh = hi[-1]["level"] > hi[-2]["level"]
    hl = lo[-1]["level"] > lo[-2]["level"]
    lh = hi[-1]["level"] < hi[-2]["level"]
    ll = lo[-1]["level"] < lo[-2]["level"]

    if hh and hl:  return {"label": "HH / HL", "bias":  1}
    if lh and ll:  return {"label": "LH / LL", "bias": -1}
    if hh and ll:  return {"label": "HH / LL", "bias":  0}
    if lh and hl:  return {"label": "LH / HL", "bias":  0}
    return None


# ─────────────────────────────────────────────────────────────────────────────
#  PREMIUM / DISCOUNT  (Fibonacci 50% of last swing range)
# ─────────────────────────────────────────────────────────────────────────────
def find_pd_zone(df: pd.DataFrame, swings: list[dict]) -> Optional[dict]:
    hi = [s for s in swings if s["type"] ==  1]
    lo = [s for s in swings if s["type"] == -1]
    if not hi or not lo:
        return None
    top = max(s["level"] for s in hi[-3:])
    bot = min(s["level"] for s in lo[-3:])
    mid = (top + bot) / 2
    last = float(df["close"].values[-1])
    zone = "premium" if last > mid else "discount"
    return {"top": top, "bot": bot, "mid": mid, "zone": zone,
            "pct": round((last - bot) / (top - bot) * 100, 1) if top != bot else 50.0}


# ─────────────────────────────────────────────────────────────────────────────
#  MAIN ANALYSIS  –  combines all signals into a single result dict
# ─────────────────────────────────────────────────────────────────────────────
def analyse(df: pd.DataFrame, symbol: str = "", timeframe: str = "") -> Optional[dict]:
    """
    Full SMC analysis on a DataFrame with columns [time, open, high, low, close, volume].
    Returns a rich dict suitable for JSON serialisation.
    """
    if df is None or len(df) < 30:
        return None

    closes = df["close"].values

    # Core indicators
    sw   = find_swings(df, length=5)
    fvgs = find_fvg(df)
    obs  = find_order_blocks(df, sw)
    bos  = find_bos_choch(df, sw)
    liq  = find_liquidity(df, sw)
    str_ = find_swing_structure(sw)
    pd_  = find_pd_zone(df, sw)

    # Latest unmitigated signals
    last_fvg = next((f for f in reversed(fvgs) if not f["mitigated"]), None)
    last_ob  = next((o for o in reversed(obs)  if not o["mitigated"]), None)

    # Price metrics
    last_price = float(closes[-1])
    prev_price = float(closes[-2]) if len(closes) > 1 else last_price
    change_pct = ((last_price - float(closes[0])) / float(closes[0])) * 100
    tick_dir   = 1 if last_price >= prev_price else -1

    # Composite bias score (weighted)
    score = 0.0
    if last_fvg: score += last_fvg["type"] * 1.0
    if last_ob:  score += last_ob["type"]  * 1.0
    if bos["bos"]:   score += bos["bos"]   * 1.0
    if bos["choch"]: score += bos["choch"] * 1.5
    if str_:     score += str_["bias"]     * 0.8
    if liq:      score += liq["type"]      * 0.5

    bias = 1 if score > 1.5 else (-1 if score < -1.5 else 0)
    confluence = min(6, round(abs(score) * 1.2))

    # Recent 30 closes for sparkline
    spark = [round(float(v), 6) for v in closes[-30:]]

    return {
        "symbol":      symbol,
        "timeframe":   timeframe,
        "price":       round(last_price, 6),
        "prev_price":  round(prev_price, 6),
        "change_pct":  round(change_pct, 4),
        "tick_dir":    tick_dir,
        "bid":         round(last_price * 0.9999, 6),
        "ask":         round(last_price * 1.0001, 6),

        # Signals
        "fvg":   last_fvg,
        "ob":    last_ob,
        "bos":   bos["bos"],
        "choch": bos["choch"],
        "bos_level":   bos["bos_level"],
        "choch_level": bos["choch_level"],
        "liquidity":   liq,
        "swing":       str_,
        "pd_zone":     pd_,

        # Bias
        "score":       round(score, 2),
        "confluence":  confluence,
        "bias":        bias,   # 1=bull, -1=bear, 0=neutral

        # Extras
        "spark":       spark,
        "candle_count": len(df),
        "swings_count": len(sw),
        "fvg_count":    len([f for f in fvgs if not f["mitigated"]]),
        "ob_count":     len([o for o in obs  if not o["mitigated"]]),
    }
