"""AAPL Edge engine: data, indicators, setups, sizing, backtest, scalp (paper).
Everything the live scanner uses is the SAME code the backtest uses."""
import time
import numpy as np
import pandas as pd
import requests

ET = "America/New_York"
H = {"User-Agent": "Mozilla/5.0"}
SLIP = 0.0003  # 3 bps each way

# Parameters were fixed from the study (stop/target in daily ATRs, max hold in days).
# tier: "core" = passed the strict tests (full size). "probation" = Medium/Low confidence (half size, tracked live).
SETUPS = {
    "gap_down": {"name": "Gap-down dip buy", "stop": 1.5, "tgt": 2.0, "hold": 5, "tier": "probation", "conf": "Medium"},
    "high52": {"name": "52-week high breakout", "stop": 1.5, "tgt": 2.0, "hold": 5, "tier": "probation", "conf": "Medium"},
    "oversold": {"name": "Oversold bounce", "stop": 2.0, "tgt": 3.0, "hold": 10, "tier": "core", "conf": "High"},
    "breakout": {"name": "Volume breakout", "stop": 1.5, "tgt": 3.0, "hold": 10, "tier": "probation", "conf": "Medium"},
    "vix_fall": {"name": "VIX falling from spike", "stop": 2.0, "tgt": 3.0, "hold": 10, "tier": "probation", "conf": "Low"},
    "rsi40": {"name": "RSI under 40 pullback", "stop": 2.0, "tgt": 3.0, "hold": 10, "tier": "probation", "conf": "Low"},
    "drop3": {"name": "Drop over 3% in uptrend", "stop": 2.0, "tgt": 3.0, "hold": 10, "tier": "probation", "conf": "Low"},
    "ibs10": {"name": "Closed at day low (IBS)", "stop": 2.0, "tgt": 3.0, "hold": 10, "tier": "probation", "conf": "Low"},
}


# ---------------------------------------------------------------- data
def fetch(sym, rng, itv, tries=4):
    last = None
    for k in range(tries):
        try:
            r = requests.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}",
                params={"range": rng, "interval": itv}, headers=H, timeout=30)
            if r.status_code == 429:
                time.sleep(3 * (k + 1)); continue
            r.raise_for_status()
            j = r.json()["chart"]["result"][0]
            q = j["indicators"]["quote"][0]
            df = pd.DataFrame(q, index=pd.to_datetime(j["timestamp"], unit="s", utc=True).tz_convert(ET))
            return df.dropna(subset=["close"])
        except Exception as e:  # noqa
            last = e; time.sleep(2)
    raise RuntimeError(f"fetch {sym} {rng} {itv} failed: {last}")


def _daily_idx(d):
    d = d.copy()
    d.index = d.index.tz_convert(ET).tz_localize(None).normalize()
    return d[~d.index.duplicated(keep="last")]


def rsi(s, n):
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn)


def build_daily(aapl, spy, vix):
    a, s, v = _daily_idx(aapl), _daily_idx(spy), _daily_idx(vix)
    df = a[["open", "high", "low", "close", "volume"]].copy()
    df["spy"] = s.close.reindex(df.index).ffill()
    df["vix"] = v.close.reindex(df.index).ffill()
    c = df.close
    tr = pd.concat([df.high - df.low, (df.high - c.shift()).abs(), (df.low - c.shift()).abs()], axis=1).max(axis=1)
    df["atr"] = tr.ewm(alpha=1 / 14, adjust=False).mean()
    df["rsi2"], df["rsi14"] = rsi(c, 2), rsi(c, 14)
    for n in (20, 50, 200):
        df[f"sma{n}"] = c.rolling(n).mean()
    df["hh20"] = df.high.rolling(20).max().shift()
    df["ll20"] = df.low.rolling(20).min().shift()
    df["vol20"] = df.volume.rolling(20).mean()
    df["vratio"] = df.volume / df.vol20
    df["gap"] = df.open / c.shift() - 1
    df["up"] = (c > df.sma200) & (df.sma50 > df.sma200)
    df["ibs"] = (c - df.low) / (df.high - df.low)
    df["ret"] = c.pct_change()
    df["hh252"] = df.high.rolling(252).max().shift()
    df["vix5max"] = df.vix.rolling(5).max()
    return df


def signals(df):
    """Boolean series: setup fired on the CLOSE of that day -> entry next open."""
    c = df.close
    return {
        "gap_down": df.up & (df.gap < -0.015),
        "oversold": df.rsi14 < 30,
        "breakout": df.up & (c > df.hh20) & (df.volume > 1.5 * df.vol20),
        "high52": df.up & (c >= df.hh252),
        "vix_fall": df.up & (df.vix5max >= 25) & (df.vix < 0.9 * df.vix5max),
        "rsi40": df.up & (df.rsi14 < 40),
        "drop3": df.up & (df.ret < -0.03),
        "ibs10": df.ibs < 0.1,
    }


# ---------------------------------------------------------------- plan / sizing
def plan_levels(entry, atr, key):
    p = SETUPS[key]
    return {"stop": round(entry - p["stop"] * atr, 2), "target": round(entry + p["tgt"] * atr, 2)}


def regime(vix, above200):
    if vix >= 30:
        return {"label": "DEFENSIVE", "mult": 0.5, "note": "VIX 30+: half size, breakout setup paused"}
    if vix >= 22:
        return {"label": "CAUTIOUS", "mult": 0.75, "note": "VIX elevated: 75% size"}
    return {"label": "NORMAL", "mult": 1.0, "note": "VIX calm: full size"}


def size(entry, stop, capital, risk_pct, max_pos_pct, mult=1.0):
    per = entry - stop
    if per <= 0:
        return {"shares": 0, "risk": 0, "cost": 0}
    sh = int((capital * risk_pct / 100 * mult) // per)
    sh = min(sh, int(capital * max_pos_pct / 100 // entry))
    return {"shares": sh, "risk": round(sh * per, 2), "cost": round(sh * entry, 2)}


# ---------------------------------------------------------------- swing backtest
def backtest(df, sig, key, p=None):
    p = p or SETUPS[key]
    o, h, l, cl, at = (df[k].values for k in ("open", "high", "low", "close", "atr"))
    sg = sig.shift(1).fillna(False).values
    idx, rows, i = df.index, [], 1
    while i < len(df):
        if sg[i] and not np.isnan(at[i - 1]):
            e = o[i] * (1 + SLIP); a_ = at[i - 1]
            s, t = e - p["stop"] * a_, e + p["tgt"] * a_
            R, ex, j = e - s, None, i
            for j in range(i, min(i + p["hold"], len(df))):
                if l[j] <= s: ex = s; break          # stop first = conservative
                if h[j] >= t: ex = t; break
            if ex is None:
                j = min(i + p["hold"], len(df)) - 1; ex = cl[j]
            ex *= (1 - SLIP)
            rows.append((idx[i], (ex - e) / R, ex / e - 1, j - i + 1)); i = j + 1
        else:
            i += 1
    return pd.DataFrame(rows, columns=["date", "R", "ret", "days"])


def stats(t):
    if len(t) == 0:
        return {"n": 0}
    w, L = t.R[t.R > 0].sum(), -t.R[t.R < 0].sum()
    return {"n": int(len(t)), "win": round(float((t.R > 0).mean() * 100), 1),
            "avgR": round(float(t.R.mean()), 2), "pf": round(float(w / L), 2) if L > 0 else None,
            "days": round(float(t.days.mean()), 1)}


# ---------------------------------------------------------------- scalp (PAPER ONLY)
def add_vwap(d):
    tp = (d.high + d.low + d.close) / 3
    day = d.index.date
    d = d.copy()
    d["vwap"] = (tp * d.volume).groupby(day).cumsum() / d.volume.groupby(day).cumsum()
    return d


def scalp_detect(day, spy_day, atr_d):
    """First opening-range breakout of the session. day/spy_day: completed 5m bars of ONE session
    (vwap column present). Returns dict or None. Long only (cash account)."""
    if len(day) < 4 or len(spy_day) < 4:
        return None
    orh, orl = day.high.iloc[:3].max(), day.low.iloc[:3].min()
    rng = orh - orl
    if not (0.15 * atr_d <= rng <= 0.8 * atr_d):
        return None
    for j in range(3, len(day)):
        r = day.iloc[j]
        if r.name.hour * 60 + r.name.minute > 14 * 60 + 30:
            return None
        sp = spy_day.reindex([r.name], method="ffill").iloc[0] if r.name in spy_day.index else None
        if sp is None or pd.isna(sp.vwap):
            continue
        if (r.close > orh and r.close > r.vwap and sp.close > sp.vwap
                and r.volume >= 1.2 * day.volume.iloc[:j].mean()):
            if r.close - orh > 0.25 * rng:
                return None  # chased; skip
            entry, stop = float(r.close), float(orl)
            risk = entry - stop
            return {"bar": j, "time": r.name, "entry": round(entry, 2), "stop": round(stop, 2),
                    "target": round(entry + 1.5 * risk, 2), "orh": round(float(orh), 2), "orl": round(float(orl), 2)}
    return None


def scalp_resolve(day, sig_bar, entry, stop, target):
    for j in range(sig_bar + 1, len(day)):
        r = day.iloc[j]
        if r.low <= stop: return "stop", stop
        if r.high >= target: return "target", target
    return None, float(day.close.iloc[-1])
