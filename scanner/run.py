"""AAPL Edge live scanner. Run every ~5 min by GitHub Actions.
 - after the close  : scans for swing setups -> "plan for tomorrow" push
 - at the open      : fills plans with the real open price -> "ENTER NOW" push
 - during session   : watches stops/targets/time-stops, runs paper scalp scan
Usage: python scanner/run.py [--dry] [--now 2026-11-09T09:40] [--asof 2026-11-06]"""
import argparse, json, os, sys, datetime as dt
import pandas as pd
import requests
import engine as E

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
STATE = os.path.join(ROOT, "docs", "state.json")
CFG = json.load(open(os.path.join(ROOT, "config.json")))
CAPITAL = float(os.environ.get("CAPITAL") or CFG["capital"])
RISK = float(os.environ.get("RISK_PCT") or CFG["risk_pct"])
MAXPOS = CFG["max_position_pct"]
TOPIC = os.environ.get("NTFY_TOPIC", "")


def load_json(p, d):
    try: return json.load(open(p))
    except Exception: return d


def push(state, key, title, body, prio=3, tags="chart_with_upwards_trend", dry=False):
    if key in state["sent"]:
        return
    state["sent"].append(key); state["sent"] = state["sent"][-400:]
    print(f"PUSH[{prio}] {title}\n   {body}")
    if dry or not TOPIC:
        return
    try:
        requests.post(f"https://ntfy.sh/{TOPIC}", data=body.encode("utf-8"),
                      headers={"Title": title, "Priority": str(prio), "Tags": tags}, timeout=15)
    except Exception as e:
        print("push failed", e)


def fmt(x): return f"${x:,.2f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true"); ap.add_argument("--now"); ap.add_argument("--asof")
    ap.add_argument("--state", default=STATE)
    a = ap.parse_args()
    now = pd.Timestamp(a.now, tz=E.ET) if a.now else pd.Timestamp.now(tz=E.ET)
    state = load_json(a.state, {})
    state.setdefault("signals", []); state.setdefault("sent", [])
    state["stats"] = load_json(os.path.join(ROOT, "docs", "stats.json"), {})
    try:
        raw = E.fetch("AAPL", "2y", "1d"); spy = E.fetch("SPY", "2y", "1d"); vix = E.fetch("^VIX", "2y", "1d")
        df = E.build_daily(raw, spy, vix)
        if a.asof: df = df[df.index <= pd.Timestamp(a.asof)]
    except Exception as e:
        state["status"] = f"DATA ERROR: {e}"; state["updated"] = now.isoformat(); save(state, a.state); print(state["status"]); return
    state["status"] = "ok"

    last_date = df.index[-1].date(); today = now.date()
    mins = now.hour * 60 + now.minute
    live = (last_date == today) and not a.asof
    in_session = live and 9 * 60 + 30 <= mins < 16 * 60
    after_close = (last_date == today and mins >= 16 * 60 + 5) or bool(a.asof)
    d0 = df.iloc[-1]; reg = E.regime(float(d0.vix), bool(d0.up))

    # live price from 5m bars (fresher than the daily bar)
    a5 = s5 = None; price = float(d0.close)
    if in_session:
        try:
            a5 = E.add_vwap(E.fetch("AAPL", "5d", "5m")); s5 = E.add_vwap(E.fetch("SPY", "5d", "5m"))
            price = float(a5.close.iloc[-1])
        except Exception as e:
            print("intraday fetch failed", e)
    prev_close = float(df.close.iloc[-2]) if len(df) > 1 else price

    # ---------- events
    ev = []
    for k in ("fomc", "earnings"):
        for d in CFG.get(k, []):
            n = (pd.Timestamp(d) - pd.Timestamp(today)).days
            if 0 <= n <= 2: ev.append({"type": k.upper(), "date": d, "days": n})
    earnings_soon = any(e["type"] == "EARNINGS" and e["days"] <= 1 for e in ev)

    open_sigs = [s for s in state["signals"] if s["kind"] == "swing" and s["status"] == "open"]

    # ---------- 1) resolve open swing trades
    for s in open_sigs:
        bars = df[df.index >= pd.Timestamp(s["entry_date"])]
        res = None
        for n, (d, r) in enumerate(bars.iterrows(), 1):
            bar_closed = d < df.index[-1] or after_close
            if r.low <= s["stop"]: res = ("stop", s["stop"], d); break
            if r.high >= s["target"]: res = ("target", s["target"], d); break
            if n >= s["hold"] and bar_closed: res = ("timeout", float(r.close), d); break
            if n >= s["hold"] and in_session and mins >= 15 * 60 + 30:
                push(state, f"{s['id']}:timestop", "AAPL time stop", f"{s['name']}: max hold reached. Sell before the close.", 4, "alarm_clock", a.dry)
        if res:
            kind, ex, d = res
            s.update(status=kind, exit=round(ex, 2), exit_date=str(d.date()),
                     R=round((ex - s["entry"]) / (s["entry"] - s["stop"]), 2))
            sz = E.size(s["entry"], s["stop"], CAPITAL, RISK, MAXPOS, s.get("mult", 1))
            pnl = (ex - s["entry"]) * sz["shares"]
            word = {"target": "TARGET HIT - sell", "stop": "STOP HIT - sell", "timeout": "TIME STOP - sell"}[kind]
            push(state, f"{s['id']}:exit", f"AAPL {word}", f"{s['name']}: exit near {fmt(ex)} ({s['R']:+.2f}R, about {pnl:+,.0f} USD on {sz['shares']} sh).",
                 5 if kind != "target" else 4, "white_check_mark" if kind == "target" else "octagonal_sign", a.dry)

    # ---------- 2) fill planned entries at the open
    if (live and mins >= 9 * 60 + 35) or a.asof:
        for s in state["signals"]:
            if s["kind"] != "swing" or s["status"] != "planned": continue
            if str(last_date) <= s["plan_date"]: continue
            bar = df.iloc[-1]; entry = float(bar.open)
            n_open = len([x for x in state["signals"] if x["kind"] == "swing" and x["status"] == "open"])
            if abs(entry - s["plan_entry"]) > 1.0 * s["atr"]:
                s["status"] = "skipped"; s["note"] = "Opened too far from plan"; continue
            if n_open >= CFG["max_open"]:
                s["status"] = "skipped"; s["note"] = "Max open positions reached"; continue
            lv = E.plan_levels(entry, s["atr"], s["setup"])
            s.update(status="open", entry=round(entry, 2), entry_date=str(last_date), **lv)
            sz = E.size(entry, lv["stop"], CAPITAL, RISK, MAXPOS, s["mult"])
            push(state, f"{s['id']}:enter", f"AAPL ENTER NOW: {s['name']}",
                 f"Buy ~{sz['shares']} sh at {fmt(entry)} (limit {fmt(entry * 1.002)}). Stop {fmt(lv['stop'])}. Target {fmt(lv['target'])}. Max hold {s['hold']} days. Risk {fmt(sz['risk'])}.",
                 5, "rotating_light", a.dry)

    # ---------- 3) scan for new swing plans (after the close only)
    if after_close:
        sg = E.signals(df)
        for k, ser in sg.items():
            if not bool(ser.iloc[-1]): continue
            if k == "breakout" and reg["label"] == "DEFENSIVE": continue
            if earnings_soon:
                continue
            sid = f"{k}-{last_date}"
            if any(x["id"] == sid for x in state["signals"]): continue
            if any(x["setup"] == k and x["status"] in ("planned", "open") for x in state["signals"]): continue
            p = E.SETUPS[k]; entry = float(d0.close); lv = E.plan_levels(entry, float(d0.atr), k)
            sig = dict(id=sid, kind="swing", setup=k, name=p["name"], created=now.isoformat(), plan_date=str(last_date),
                       plan_entry=round(entry, 2), atr=round(float(d0.atr), 2), hold=p["hold"], stop_k=p["stop"], tgt_k=p["tgt"],
                       mult=reg["mult"], status="planned", **lv)
            state["signals"].append(sig)
            sz = E.size(entry, lv["stop"], CAPITAL, RISK, MAXPOS, reg["mult"])
            st = state["stats"].get(k, {}).get("all", {})
            push(state, f"{sid}:plan", f"AAPL plan for tomorrow: {p['name']}",
                 f"Buy at the open if near {fmt(entry)}. Stop ~{fmt(lv['stop'])}, target ~{fmt(lv['target'])}, hold up to {p['hold']} days, ~{sz['shares']} sh. "
                 f"Backtest: {st.get('win', '?')}% win, {st.get('avgR', '?')}R avg. Final levels arrive at 9:35 AM.",
                 4, "calendar", a.dry)
        # expire stale plans
        for s in state["signals"]:
            if s["status"] == "planned" and (pd.Timestamp(last_date) - pd.Timestamp(s["plan_date"])).days > 4:
                s["status"] = "expired"

    # ---------- 4) paper scalp (opening range breakout), session only
    if in_session and a5 is not None and CFG.get("scalp_enabled", True):
        try:
            bars = a5[a5.index.date == today]; sp = s5[s5.index.date == today]
            bars = bars[bars.index + pd.Timedelta(minutes=5) <= now]; sp = sp[sp.index + pd.Timedelta(minutes=5) <= now]
            sid = f"scalp-{today}"
            mine = next((x for x in state["signals"] if x["id"] == sid), None)
            if mine is None and len(bars) >= 4:
                det = E.scalp_detect(bars, sp, float(df.atr.iloc[-1]))
                if det:
                    sig = dict(id=sid, kind="scalp", setup="scalp_orb", name="Opening-range scalp (PAPER)", created=now.isoformat(),
                               plan_date=str(today), entry=det["entry"], stop=det["stop"], target=det["target"], status="open",
                               entry_date=str(today), hold=1, orh=det["orh"], orl=det["orl"], mult=reg["mult"])
                    state["signals"].append(sig)
                    sz = E.size(det["entry"], det["stop"], CAPITAL, RISK, MAXPOS, reg["mult"])
                    push(state, f"{sid}:enter", "AAPL PAPER scalp (not validated - do not trade yet)",
                         f"Broke the 15-min range high {fmt(det['orh'])}. Paper entry {fmt(det['entry'])}, stop {fmt(det['stop'])}, target {fmt(det['target'])}. Logged only.",
                         2, "test_tube", a.dry)
            elif mine and mine["status"] == "open":
                px = bars[bars.index > pd.Timestamp(mine["created"])]
                for _, r in px.iterrows():
                    if r.low <= mine["stop"]: out, ex = "stop", mine["stop"]; break
                    if r.high >= mine["target"]: out, ex = "target", mine["target"]; break
                else:
                    out, ex = (("timeout", price) if mins >= 15 * 60 + 50 else (None, None))
                if out:
                    mine.update(status=out, exit=round(ex, 2), exit_date=str(today), R=round((ex - mine["entry"]) / (mine["entry"] - mine["stop"]), 2))
        except Exception as e:
            print("scalp scan error", e)

    # ---------- state for the dashboard
    c = df.close
    def pct(x): return round(float(x) * 100, 2)
    watch = [
        {"key": "oversold", "name": "Oversold bounce", "text": f"RSI14 is {d0.rsi14:.0f}; triggers below 30", "progress": max(0, min(1, (70 - d0.rsi14) / 40)), "fired": bool(d0.rsi14 < 30)},
        {"key": "breakout", "name": "Volume breakout", "text": f"Price {fmt(d0.close)} vs 20-day high {fmt(d0.hh20)}; volume {d0.vratio:.1f}x avg (needs 1.5x)",
         "progress": max(0, min(1, (c.iloc[-1] / d0.hh20 - 0.95) / 0.05)) if d0.up else 0, "fired": bool(E.signals(df)["breakout"].iloc[-1])},
        {"key": "gap_down", "name": "Gap-down dip buy", "text": f"Today's gap {pct(d0.gap):+.2f}% (needs below -1.5%) in an uptrend" if d0.up else "Needs uptrend (above 200-day, 50 over 200)",
         "progress": max(0, min(1, -d0.gap / 0.015)) if d0.up else 0, "fired": bool(E.signals(df)["gap_down"].iloc[-1])},
    ]
    closed = [s for s in state["signals"] if s["kind"] == "swing" and s["status"] in ("target", "stop", "timeout")]
    state.update(
        updated=now.isoformat(), session="open" if in_session else "closed", price=round(price, 2), prev_close=round(prev_close, 2),
        chg_pct=round((price / prev_close - 1) * 100, 2), vix=round(float(d0.vix), 2), regime=reg, events=ev,
        trend={"up": bool(d0.up), "sma50": round(float(d0.sma50), 2), "sma200": round(float(d0.sma200), 2),
               "above200": bool(c.iloc[-1] > d0.sma200)},
        ind={"rsi14": round(float(d0.rsi14), 1), "atr": round(float(d0.atr), 2), "atr_pct": round(float(d0.atr / d0.close * 100), 2),
             "hh20": round(float(d0.hh20), 2), "ll20": round(float(d0.ll20), 2), "vratio": round(float(d0.vratio), 2), "bar_date": str(last_date)},
        watch=watch,
        risk_cfg={"capital": None, "risk_pct": RISK},  # capital stays private; the dashboard uses your phone's setting
        journal={"swing": E.stats(pd.DataFrame([{"R": s["R"], "ret": 0, "days": 1, "date": s["exit_date"]} for s in closed])) if closed else {"n": 0}},
    )
    save(state, a.state)
    print("state saved. signals:", [(s["id"], s["status"]) for s in state["signals"][-6:]])


def save(state, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(state, open(path, "w"), indent=1, default=str)


if __name__ == "__main__":
    main()
