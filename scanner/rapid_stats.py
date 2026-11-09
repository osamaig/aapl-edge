"""Builds docs/rapid_stats.json from the 2-year hourly backtest of the Rapid rule (+$2, 2 lots)."""
import json, os, numpy as np
import engine as E, rapid as R
root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
cfg = json.load(open(os.path.join(root, "config.json"))); R.P["dip"] = cfg.get("rapid_dip", 0.003)
daily = E.build_daily(E.fetch("AAPL", "2y", "1d"), E.fetch("SPY", "2y", "1d"), E.fetch("^VIX", "2y", "1d"))
a = E.fetch("AAPL", "730d", "1h"); s = E.fetch("SPY", "730d", "1h")
wk = a.index.normalize().nunique() / 5
t = R.apply_cap(R.backtest(a, s, daily, 60, pct=cfg.get("rapid_target", 2.0) / 350, lot_cap=False), cfg.get("rapid_lots", 2))
c = t.cal_days.fillna(9999)
out = dict(n=int(len(t)), per_week=round(len(t) / wk, 2), same_day=round(float(np.mean(c == 0)) * 100), within3=round(float(np.mean(c <= 3)) * 100),
           within10=round(float(np.mean(c <= 10)) * 100), p90_drop=round(float(t.dd.quantile(.9)) * 100, 1), worst_drop=round(float(t.dd.max()) * 100, 1),
           longest_days=int(t.cal_days.max()), lots=cfg.get("rapid_lots", 2), target=cfg.get("rapid_target", 2.0))
json.dump(out, open(os.path.join(root, "docs", "rapid_stats.json"), "w"), indent=1); print(out)
