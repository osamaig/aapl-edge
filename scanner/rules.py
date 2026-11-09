"""Library of standard analyst entry rules (long only, evaluated on the daily close).
The SAME functions are used by the backtest and by the live scanner/dashboard."""
import numpy as np
import pandas as pd
import engine as E


def add_ind(df):
    d = df.copy(); c = d.close
    e12, e26 = c.ewm(span=12, adjust=False).mean(), c.ewm(span=26, adjust=False).mean()
    d["macd"] = e12 - e26; d["macd_sig"] = d.macd.ewm(span=9, adjust=False).mean(); d["macd_hist"] = d.macd - d.macd_sig
    m, sd = c.rolling(20).mean(), c.rolling(20).std()
    d["bb_mid"], d["bb_up"], d["bb_lo"] = m, m + 2 * sd, m - 2 * sd
    d["bb_bw"] = (d.bb_up - d.bb_lo) / m
    lo14, hi14 = d.low.rolling(14).min(), d.high.rolling(14).max()
    d["stoch_k"] = 100 * (c - lo14) / (hi14 - lo14); d["stoch_d"] = d.stoch_k.rolling(3).mean()
    return d


def _x_up(a, b): return (a > b) & (a.shift() <= b.shift())


# key: (name, plain-English description, function(df) -> bool Series)
RULES = {
    "rsi_lt30": ("RSI under 30", "Oversold: RSI(14) below 30", lambda d: d.rsi14 < 30),
    "rsi_lt35_up": ("RSI under 35 in uptrend", "Dip inside an uptrend: RSI(14) below 35, price above 200-day", lambda d: (d.rsi14 < 35) & d.up),
    "rsi_cross30": ("RSI crosses back above 30", "Oversold bounce confirmed: RSI(14) crosses up through 30", lambda d: _x_up(d.rsi14, pd.Series(30.0, index=d.index))),
    "rsi2_lt10_up": ("RSI(2) under 10 in uptrend", "Very short-term washout in an uptrend", lambda d: (d.rsi2 < 10) & d.up),
    "macd_cross": ("MACD bullish cross", "MACD line crosses above its signal line", lambda d: _x_up(d.macd, d.macd_sig)),
    "macd_cross_neg": ("MACD cross below zero", "MACD crosses up while still below zero (early reversal)", lambda d: _x_up(d.macd, d.macd_sig) & (d.macd < 0)),
    "golden_20_50": ("20-day crosses above 50-day", "Short average crosses above medium average", lambda d: _x_up(d.sma20, d.sma50)),
    "close_x_sma20": ("Close crosses above 20-day", "Price reclaims its 20-day average", lambda d: _x_up(d.close, d.sma20)),
    "pull_sma20_up": ("Pullback to 20-day in uptrend", "Uptrend, low touches the 20-day, closes back above it", lambda d: d.up & (d.low <= d.sma20) & (d.close > d.sma20)),
    "pull_sma50_up": ("Pullback to 50-day in uptrend", "Uptrend, low touches the 50-day, closes back above it", lambda d: d.up & (d.low <= d.sma50) & (d.close > d.sma50)),
    "bb_lower": ("Close below lower Bollinger band", "Price closes under the 20-day, 2-sigma band", lambda d: d.close < d.bb_lo),
    "bb_reclaim": ("Back inside lower Bollinger band", "Closed below the band yesterday, back inside today", lambda d: (d.close.shift() < d.bb_lo.shift()) & (d.close > d.bb_lo)),
    "bb_squeeze": ("Bollinger squeeze breakout", "Bands tightest in 120 days, then close above the upper band", lambda d: (d.bb_bw.shift() <= d.bb_bw.rolling(120).quantile(0.1).shift()) & (d.close > d.bb_up)),
    "stoch_cross": ("Stochastic cross up under 20", "Stochastic %K crosses above %D while below 20", lambda d: _x_up(d.stoch_k, d.stoch_d) & (d.stoch_k < 20)),
    "vol_spike_up": ("Volume spike on up day", "Volume 2x average and price up", lambda d: (d.vratio > 2) & (d.ret > 0)),
    "break20": ("20-day high breakout", "Close above the prior 20-day high", lambda d: d.close > d.hh20),
    "gap_fill_up": ("Gap down then closes green", "Opens 0.5%+ lower, closes above the open", lambda d: (d.gap < -0.005) & (d.close > d.open)),
    "inside_break": ("Inside day breakout", "Yesterday inside the prior bar, today closes above its high", lambda d: ((d.high.shift() < d.high.shift(2)) & (d.low.shift() > d.low.shift(2))) & (d.close > d.high.shift())),
    "three_down_up": ("3 down days in uptrend", "Three lower closes in a row inside an uptrend", lambda d: d.up & (d.close < d.close.shift()) & (d.close.shift() < d.close.shift(2)) & (d.close.shift(2) < d.close.shift(3))),
    "hammer": ("Hammer after selling", "Closes in top 30% of range after two down days", lambda d: (d.ibs > 0.7) & (d.close.shift() < d.close.shift(2)) & (d.close.shift(2) < d.close.shift(3))),
}


def evaluate(df):
    d = add_ind(df)
    return {k: v[2](d).fillna(False) for k, v in RULES.items()}
