"""AAPL Rapid: buy a dip in an uptrend, sell about $2 higher, hold until green (no stop). Lot cap keeps cash from piling up.
The same signal() runs in the backtest and live."""
import numpy as np, pandas as pd

P = dict(dip=0.005,          # price is 0.5% (about $1.75) below today's high
         max_drop_prev=0.015,  # but not down more than 1.5% vs yesterday's close (no falling knives)
         spy_min=-0.012,       # market not falling hard today
         vix_max=30,
         t_start=10 * 60, t_end=15 * 60 + 30,
         target_usd=2.0, target2_usd=3.0)


def mins(ts): return ts.hour * 60 + ts.minute


def signal(day, prev_close, up, vix, spy_ret, res):
    """day = today's COMPLETED bars (high/low/close columns). Returns dict or None."""
    if len(day) < 2 or not up or vix >= P["vix_max"] or spy_ret <= P["spy_min"]:
        return None
    t_end = mins(day.index[-1]) + res
    if not (P["t_start"] <= t_end <= P["t_end"]):
        return None
    price, hi, lo = float(day.close.iloc[-1]), float(day.high.max()), float(day.low.min())
    if (hi - price) / hi >= P["dip"] and price >= prev_close * (1 - P["max_drop_prev"]) and price > lo * 1.001:
        return {"price": round(price, 2), "day_high": round(hi, 2), "dip_pct": round((hi - price) / hi * 100, 2)}
    return None


def backtest(a, s, daily, res, pct=None, lot_cap=True):
    """a, s: AAPL / SPY intraday bars. daily: engine frame (close, sma200, vix). One lot at a time (waits for green).
    Returns DataFrame of trades: entry_date, entry, days, dd (worst drop from entry before exit), exit_date."""
    pct = pct or 0.0057
    dd_idx = daily.index.date
    dclose = pd.Series(daily.close.values, index=dd_idx); dup = pd.Series(daily.up.values, index=dd_idx); dvix = pd.Series(daily.vix.values, index=dd_idx)
    A = a.reset_index(); times = a.index; dates = np.array([t.date() for t in times])
    rows, blocked_until, n = [], -1, len(a)
    days = sorted(set(dates))
    for d in days:
        prev = [x for x in dd_idx if x < d]
        if not prev: continue
        pc, pu, pv = float(dclose[prev[-1]]), bool(dup[prev[-1]]), float(dvix[prev[-1]])
        ii = np.where(dates == d)[0]
        if len(ii) < (6 if res >= 60 else 60) or ii[0] <= blocked_until: continue
        day = a.iloc[ii]; sp = s[s.index.date == d]
        for j in range(1, len(day)):
            sofar = day.iloc[: j + 1]
            sps = sp[sp.index <= sofar.index[-1]]
            spy_ret = float(sps.close.iloc[-1] / pc_spy(sps, daily, d) - 1) if len(sps) else 0.0
            if signal(sofar, pc, pu, pv, spy_ret, res):
                k = ii[0] + j + 1
                if k >= n: break
                e = float(a.open.iloc[k]) * 1.0004; tgt = e * (1 + pct); dd = 0.0; ex = None
                for m in range(k, n):
                    dd = max(dd, (e - float(a.low.iloc[m])) / e)
                    if a.high.iloc[m] >= tgt: ex = m; break
                if ex is None: rows.append((d, e, np.nan, dd, None, k, n)); blocked_until = n; break
                rows.append((d, e, (np.datetime64(times[ex].date()) - np.datetime64(d)).astype(int), dd, times[ex].date(), k, ex))
                if lot_cap: blocked_until = ex
                break
    return pd.DataFrame(rows, columns=["date", "entry", "cal_days", "dd", "exit_date", "k", "ex"])


def pc_spy(sps, daily, d):
    return float(daily.spy[[x < d for x in daily.index.date]].iloc[-1])


def apply_cap(t, max_lots):
    """Keep entries only while fewer than max_lots earlier lots are still open (waiting for green)."""
    keep, open_ex = [], []
    for _, r in t.sort_values("k").iterrows():
        open_ex = [x for x in open_ex if x > r.k]
        if len(open_ex) < max_lots:
            keep.append(r); open_ex.append(r.ex)
    return pd.DataFrame(keep)
