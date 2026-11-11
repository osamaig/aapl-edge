"""Builds docs/rapid_stats.json: backtest of the Rapid rule for every stock in config rapid_symbols
(~3 years hourly bars, trend data from 15 years of daily bars, up to rapid_lots open at once, each stock tested alone)."""
import json, os, numpy as np
import engine as E, rapid as R
root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
cfg = json.load(open(os.path.join(root, "config.json"))); R.P["dip"] = cfg.get("rapid_dip", 0.003)
pct = cfg.get("rapid_target_pct", 0.57) / 100
spy, vix, spy_h = E.fetch("SPY", "15y", "1d"), E.fetch("^VIX", "15y", "1d"), E.fetch("SPY", "730d", "1h")
out = {"by": {}, "lots": cfg.get("rapid_lots", 2), "target_pct": cfg.get("rapid_target_pct", 0.57)}
for sym in cfg.get("rapid_symbols", ["AAPL"]):
    try:
        daily = E.build_daily(E.fetch(sym, "15y", "1d"), spy, vix); a = E.fetch(sym, "730d", "1h"); wk = a.index.normalize().nunique() / 5
        t = R.apply_cap(R.backtest(a, spy_h, daily, 60, pct=pct, lot_cap=False), out["lots"]); c = t.cal_days.fillna(9999)
        out["by"][sym] = dict(n=int(len(t)), per_week=round(len(t) / wk, 2), same_day=round(float(np.mean(c == 0)) * 100), within3=round(float(np.mean(c <= 3)) * 100),
                              within10=round(float(np.mean(c <= 10)) * 100), p90_drop=round(float(t.dd.quantile(.9)) * 100, 1), worst_drop=round(float(t.dd.max()) * 100, 1),
                              over10=int((t.dd > 0.10).sum()), over20=int((t.dd > 0.20).sum()), longest_days=int(t.cal_days.max()))
        print(sym, out["by"][sym], flush=True)
    except Exception as e:
        print("skip", sym, e)
json.dump(out, open(os.path.join(root, "docs", "rapid_stats.json"), "w"), indent=1)
