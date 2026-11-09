"""Backtests every rule vs a random-entry baseline. Writes docs/rules.json.
A rule 'passes' only if it beats baseline in BOTH train (before 2022) and test (2022+), on AAPL, and on most of 12 other large caps."""
import json, os, sys, numpy as np, pandas as pd
import engine as E, rules as RL
P = dict(stop=1.5, tgt=2.0, hold=5)
SPLIT = pd.Timestamp("2022-01-01"); PCT = 0.0057
OTHERS = ["MSFT", "NVDA", "AMZN", "GOOGL", "META", "JPM", "XOM", "JNJ", "PG", "KO", "WMT", "HD"]


def hit3(df, sig):
    """share of signals (next open entry) that reach +0.57% within 3 days"""
    s = sig.shift(1).fillna(False).values; o, h = df.open.values, df.high.values; r = []
    for i in np.where(s)[0]:
        if i + 3 <= len(df): r.append(h[i:i + 3].max() >= o[i] * (1 + PCT))
    return (float(np.mean(r)) * 100 if r else np.nan), len(r)


def run(df):
    sg = RL.evaluate(df); base = pd.Series(True, index=df.index)
    slow = (df.atr / df.close) < (df.atr / df.close).median()
    out = {}
    bt = E.backtest(df, base, "x", P); bs = E.backtest(df, base & slow, "x", P)
    b = {"tr": bt[bt.date < SPLIT].R.mean(), "te": bt[bt.date >= SPLIT].R.mean(), "slow": bs.R.mean(), "hit": hit3(df, base)[0]}
    for k, s in sg.items():
        t = E.backtest(df, s, k, P); ts = E.backtest(df, s & slow, k, P)
        tr, te = t[t.date < SPLIT], t[t.date >= SPLIT]
        h, hn = hit3(df, s)
        out[k] = dict(n=len(t), n_tr=len(tr), n_te=len(te), R_tr=tr.R.mean(), R_te=te.R.mean(), R=t.R.mean(), win=(t.R > 0).mean() * 100 if len(t) else np.nan,
                      slow_n=len(ts), slow_R=ts.R.mean(), hit=h, hit_n=hn)
    return b, out


def main():
    spy, vix = E.fetch("SPY", "15y", "1d"), E.fetch("^VIX", "15y", "1d")
    A = E.build_daily(E.fetch("AAPL", "15y", "1d"), spy, vix)
    b, res = run(A)
    other = {}
    for sym in OTHERS:
        try:
            ob, orr = run(E.build_daily(E.fetch(sym, "15y", "1d"), spy, vix))
            other[sym] = {k: (v["R"] - (ob["tr"] + ob["te"]) / 2) for k, v in orr.items()}
        except Exception as e:
            print("skip", sym, e)
    rows = []
    for k, v in res.items():
        up_tr, up_te = v["R_tr"] - b["tr"], v["R_te"] - b["te"]
        oth = [other[s][k] for s in other if not np.isnan(other[s][k])]
        n_pos = sum(1 for x in oth if x > 0)
        ok = (v["n"] >= 40 and up_tr > 0.05 and up_te > 0.05 and v["n_te"] >= 10 and len(oth) >= 8 and n_pos >= 0.7 * len(oth))
        rows.append(dict(key=k, name=RL.RULES[k][0], desc=RL.RULES[k][1], n=v["n"], win=round(v["win"], 1), avgR=round(v["R"], 2),
                         up_train=round(up_tr, 2), up_test=round(up_te, 2), slow_n=v["slow_n"], slow_R=None if np.isnan(v["slow_R"]) else round(v["slow_R"], 2),
                         hit3=None if np.isnan(v["hit"]) else round(v["hit"]), others_pos=f"{n_pos}/{len(oth)}", passed=bool(ok), tier="proven" if ok else ("aapl_only" if (v["n"] >= 30 and up_tr > 0.05 and up_te > 0.05) else "weak")))
    rows.sort(key=lambda r: ({"proven": 0, "aapl_only": 1, "weak": 2}[r["tier"]], -(r["up_test"] + r["up_train"])))
    import itertools
    sg = RL.evaluate(A); keys = [r["key"] for r in rows if r["tier"] != "weak"]; combos = []
    for a_, b_ in itertools.combinations(keys, 2):
        t = E.backtest(A, sg[a_] & sg[b_], "x", P)
        if len(t): combos.append(dict(rules=[a_, b_], n=int(len(t)), win=round(float((t.R > 0).mean() * 100), 1), avgR=round(float(t.R.mean()), 2)))
    out = dict(combos=combos, baseline=dict(avgR=round((b["tr"] + b["te"]) / 2, 2), slow_R=round(b["slow"], 2), hit3=round(b["hit"])), exits=P, rules=rows)
    json.dump(out, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs", "rules.json"), "w"), indent=1)
    print("baseline", out["baseline"])
    print(f"{'rule':34}{'n':>4}{'win%':>6}{'avgR':>6}{'trn+':>6}{'tst+':>6}{'slowR':>6}{'hit3d':>6}{'others':>7} pass")
    for r in rows:
        print(f"{r['name'][:33]:34}{r['n']:>4}{r['win']:>6}{r['avgR']:>6}{r['up_train']:>6}{r['up_test']:>6}{str(r['slow_R']):>6}{str(r['hit3']):>6}{r['others_pos']:>7} {r['tier']}")


if __name__ == "__main__":
    main()
