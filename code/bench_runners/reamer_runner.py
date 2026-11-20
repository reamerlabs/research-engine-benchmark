#!/usr/bin/env python3
"""Runs one Reamer Research backtest through the `reamer_research` Python
package and prints a single JSON result line to stdout. Isolated in its own
process so peak-RSS measurement is clean per engine.

Usage: reamer_runner.py <bin_path> <minimal|realistic>

Needs the Reamer Research kit's wheel (4.10.0 or later):
  pip install <kit>/python/reamer_research-*.whl
Without a licence the engine runs in Evaluation Mode (one ticker, at most 30
days of bars or 50,000 bars), which raises rr.EvaluationLimit on the
published tiers; a licence removes the limits.

Updated 2026-11-20 for the 4.10+ package. The published results were produced
by the 4.6.0 version of this file at commit aa2eae4.
"""
import json
import resource
import sys
import time
from collections import deque

import numpy as np

T0 = time.perf_counter()

import reamer_research as rr

TICKER = "BENCH"
CHANNEL = 20
SIZE = 1000.0
BAR_DTYPE = np.dtype([
    ("ts", "<i8"), ("open", "<f8"), ("high", "<f8"),
    ("low", "<f8"), ("close", "<f8"), ("volume", "<f8"),
])


class MinimalStrategy:
    lookback = 1

    def on_bar(self, data):
        _ = data[TICKER].close[-1]
        return None


class RealisticStrategy:
    """20-bar Donchian breakout, channel over the 20 bars before the current
    one. Target +1000 on a close above the channel high, -1000 on a close
    below the channel low, reached with one order sized to reverse. Same
    rule as every other runner in bench_runners/. position() is the state
    after this bar's fills (EXECUTION_SPEC.md §6), so the order is the target
    minus the position."""
    lookback = 1

    def __init__(self):
        self.highs = deque(maxlen=CHANNEL)
        self.lows = deque(maxlen=CHANNEL)
        self.target = 0.0

    def on_bar(self, data):
        tv = data[TICKER]
        high, low, close = float(tv.high[-1]), float(tv.low[-1]), float(tv.close[-1])
        order = None
        if len(self.highs) == CHANNEL:
            if close > max(self.highs) and self.target <= 0:
                self.target = SIZE
            elif close < min(self.lows) and self.target >= 0:
                self.target = -SIZE
            position = data.position(TICKER)
            if position < self.target:
                order = rr.buy_market(self.target - position, ticker=TICKER)
            elif position > self.target:
                order = rr.sell_market(position - self.target, ticker=TICKER)
        self.highs.append(high)
        self.lows.append(low)
        return order


def main():
    bin_path, strategy = sys.argv[1], sys.argv[2]
    raw = np.fromfile(bin_path, dtype=BAR_DTYPE)
    bars = np.zeros(len(raw), dtype=rr.BAR_DTYPE)
    for f in BAR_DTYPE.names:
        bars[f] = raw[f]
    bars["tick_count"] = -1

    config = {
        "initial_capital": 10_000_000.0,
        "leverage": 1.0,
        "commission_per_unit": 0.0,
        "commission_mode": "round_trip",
        "slippage": 0.0,
        "price_volatility": 0.0,
        "spread": 0.0,
        "ohlcv_type": "bid",
        "rng_seed": 42,
        "roll_days_before_monthend": 5,
    }
    strat_cls = MinimalStrategy if strategy == "minimal" else RealisticStrategy

    t_run = time.perf_counter()
    try:
        result = rr.backtest(strat_cls, {TICKER: bars}, **config)
    except rr.EvaluationLimit as e:
        sys.exit(str(e))
    run_s = time.perf_counter() - t_run

    print(json.dumps({
        "engine": "reamer_research_py",
        "strategy": strategy,
        "bars": len(bars),
        "run_s": round(run_s, 3),
        "total_s": round(time.perf_counter() - T0, 3),
        "fills": int(result.trades),
        "round_trips": int(result.closed_trades_count),
        "net_pnl": round(float(result.net_pnl), 6),
        "peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
    }))


if __name__ == "__main__":
    main()
