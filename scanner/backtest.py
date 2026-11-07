"""Re-runs the study and writes docs/stats.json (shown on the dashboard).
Run: python scanner/backtest.py   (takes ~20 seconds)"""
import json, os, datetime as dt
import numpy as np, pandas as pd
import engine as E

OUT = os.path.join(os.path.dirname(__file__), "..", "docs", "stats.json")
SPLIT = "2022-01-01"


def main():
    df = E.build_daily(E.fetch("AAPL", "15y", "1d"), E.fetch("SPY", "15y", "1d"), E.fetch("^VIX", "15y", "1d"))
    sigs = E.signals(df)
    res = {}
    for k, s in sigs.items():
        t = E.backtest(df, s, k)
        base = E.backtest(df, pd.Series(True, index=df.index), k)
        res[k] = {"name": E.SETUPS[k]["name"], "params": E.SETUPS[k],
                  "all": E.stats(t), "train": E.stats(t[t.date < SPLIT]), "test": E.stats(t[t.date >= SPLIT]),
                  "baseline": E.stats(base), "baseline_test": E.stats(base[base.date >= SPLIT])}
    # scalp: opening-range breakout on the last ~60 sessions of 5m data (tiny sample, informational only)
    a5, s5 = E.add_vwap(E.fetch("AAPL", "60d", "5m")), E.add_vwap(E.fetch("SPY", "60d", "5m"))
    atr = df.atr.copy(); atr.index = atr.index.date
    Rs = []
    for d, day in a5.groupby(a5.index.date):
        sp = s5[s5.index.date == d]
        prev = [x for x in atr.index if x < d]
        if len(day) < 60 or not prev: continue
        sg = E.scalp_detect(day, sp, float(atr[prev[-1]]))
        if not sg: continue
        out, ex = E.scalp_resolve(day, sg["bar"], sg["entry"], sg["stop"], sg["target"])
        R = (ex - sg["entry"]) / (sg["entry"] - sg["stop"]); Rs.append(R)
    Rs = pd.Series(Rs, dtype=float)
    res["scalp_orb"] = {"name": "Opening-range scalp (PAPER)", "sessions": int(a5.index.normalize().nunique()),
                        "n": int(len(Rs)), "win": round(float((Rs > 0).mean() * 100), 1) if len(Rs) else None,
                        "avgR": round(float(Rs.mean()), 2) if len(Rs) else None,
                        "note": "Only ~60 sessions of free 5-minute data exist. Too small to trust: paper-trade first."}
    res["meta"] = {"built": dt.datetime.utcnow().isoformat() + "Z", "from": str(df.index[0].date()),
                   "to": str(df.index[-1].date()), "split": SPLIT}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(res, open(OUT, "w"), indent=1)
    for k, v in res.items():
        if k != "meta": print(k, {x: v[x] for x in v if x in ("all", "test", "baseline_test", "n", "win", "avgR")})


if __name__ == "__main__":
    main()
