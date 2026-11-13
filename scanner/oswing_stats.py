"""Builds docs/oswing_stats.json: backtest of the Oversold bounce swing (daily RSI14 under 30) on each stock in config oswing_symbols.
Entry next open, stop 2.0 ATR, target 3.0 ATR, max hold 10 days, one trade at a time per stock, 15 years of daily bars."""
import json, os, numpy as np
import engine as E
root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
cfg = json.load(open(os.path.join(root, "config.json")))
spy, vix = E.fetch("SPY", "15y", "1d"), E.fetch("^VIX", "15y", "1d")
out = {"by": {}}
for sym in cfg.get("oswing_symbols", ["GOOGL", "WMT", "PG"]):
    try:
        d = E.build_daily(E.fetch(sym, "15y", "1d"), spy, vix)
        o, h, l, c, atr, r14 = d.open.values, d.high.values, d.low.values, d.close.values, d.atr.values, d.rsi14.values
        Rs, i, n = [], 0, len(d)
        times = []
        while i < n - 2:
            if r14[i] < 30 and atr[i] > 0:
                e = o[i + 1] * 1.0003; stop = e - 2.0 * atr[i]; tgt = e + 3.0 * atr[i]; ex = None
                for k in range(i + 1, min(i + 11, n)):
                    if l[k] <= stop: ex = stop; break
                    if h[k] >= tgt: ex = tgt; break
                    last = k
                if ex is None: ex = c[last]
                Rs.append((d.index[i], (ex - e) / (e - stop))); i = k + 1 if k > i else i + 1
            else: i += 1
        R = np.array([x[1] for x in Rs])
        out["by"][sym] = dict(n=int(len(R)), win=round(float((R > 0).mean()) * 100), avgR=round(float(R.mean()), 2),
                              avgR_recent=round(float(np.mean([x[1] for x in Rs if x[0].year >= 2022])), 2) if any(x[0].year >= 2022 for x in Rs) else None)
        print(sym, out["by"][sym], flush=True)
    except Exception as e:
        print("skip", sym, e)
json.dump(out, open(os.path.join(root, "docs", "oswing_stats.json"), "w"), indent=1)
