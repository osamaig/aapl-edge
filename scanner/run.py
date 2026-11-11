"""AAPL Edge live scanner. Run every ~5 min by GitHub Actions.
 - after the close  : scans for swing setups -> "plan for tomorrow" push
 - at the open      : fills plans with the real open price -> "ENTER NOW" push
 - during session   : watches swing stops/targets; AAPL Rapid dip alerts (buy dip, sell +$2, hold until green)
Usage: python scanner/run.py [--dry] [--now 2026-11-09T09:40] [--asof 2026-11-06]"""
import argparse, json, os, sys, datetime as dt
import pandas as pd
import requests
import engine as E
import rapid as R
import rules as RL

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

    for s in state["signals"]:  # keep labels current; retire the old paper-scalp experiment
        if s.get("kind") == "swing" and s.get("setup") in E.SETUPS:
            s["tier"], s["conf"] = E.SETUPS[s["setup"]]["tier"], E.SETUPS[s["setup"]]["conf"]
        if s.get("kind") == "scalp" and s.get("status") == "open":
            s["status"] = "retired"
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
                 (3 if s.get("tier") == "probation" else (5 if kind != "target" else 4)), "white_check_mark" if kind == "target" else "octagonal_sign", a.dry)

    # ---------- 2) fill planned entries at the open
    if (live and mins >= 9 * 60 + 35) or a.asof:
        for s in state["signals"]:
            if s["kind"] != "swing" or s["status"] != "planned": continue
            if str(last_date) <= s["plan_date"]: continue
            bar = df.iloc[-1]; entry = float(bar.open)
            tier = s.get("tier", "core")
            n_open = len([x for x in state["signals"] if x["kind"] == "swing" and x["status"] == "open" and x.get("tier", "core") == tier])
            if abs(entry - s["plan_entry"]) > 1.0 * s["atr"]:
                s["status"] = "skipped"; s["note"] = "Opened too far from plan"; continue
            if n_open >= CFG["max_open"]:
                s["status"] = "skipped"; s["note"] = "Max open positions reached"; continue
            lv = E.plan_levels(entry, s["atr"], s["setup"])
            s.update(status="open", entry=round(entry, 2), entry_date=str(last_date), **lv)
            sz = E.size(entry, lv["stop"], CAPITAL, RISK, MAXPOS, s["mult"])
            tag = f"ENTER NOW ({s.get('conf', 'Medium')} confidence, half size)" if tier == "probation" else "ENTER NOW (High confidence)"
            push(state, f"{s['id']}:enter", f"AAPL {tag}: {s['name']}",
                 f"Buy ~{sz['shares']} sh at {fmt(entry)} (limit {fmt(entry * 1.002)}). Stop {fmt(lv['stop'])}. Target {fmt(lv['target'])}. Max hold {s['hold']} days. Risk {fmt(sz['risk'])}.",
                 5 if tier == "core" else 3, "rotating_light" if tier == "core" else "test_tube", a.dry)

    # ---------- 3) scan for new swing plans (after the close only)
    if after_close:
        sg = E.signals(df)
        for k, ser in sg.items():
            if not bool(ser.iloc[-1]): continue
            if k in ("breakout", "high52") and reg["label"] == "DEFENSIVE": continue
            if earnings_soon:
                continue
            sid = f"{k}-{last_date}"
            if any(x["id"] == sid for x in state["signals"]): continue
            if any(x["setup"] == k and x["status"] in ("planned", "open") for x in state["signals"]): continue
            p = E.SETUPS[k]; entry = float(d0.close); lv = E.plan_levels(entry, float(d0.atr), k)
            probation = p["tier"] == "probation"
            mult = reg["mult"] * (0.5 if probation else 1.0)
            sig = dict(id=sid, kind="swing", setup=k, name=p["name"], tier=p["tier"], conf=p["conf"], created=now.isoformat(), plan_date=str(last_date),
                       plan_entry=round(entry, 2), atr=round(float(d0.atr), 2), hold=p["hold"], stop_k=p["stop"], tgt_k=p["tgt"],
                       mult=mult, status="planned", **lv)
            state["signals"].append(sig)
            sz = E.size(entry, lv["stop"], CAPITAL, RISK, MAXPOS, mult)
            st = state["stats"].get(k, {}).get("all", {})
            label = f"plan for tomorrow ({p['conf']} confidence, half size)" if probation else f"plan for tomorrow ({p['conf']} confidence)"
            push(state, f"{sid}:plan", f"AAPL {label}: {p['name']}",
                 f"Buy at the open if near {fmt(entry)}. Stop ~{fmt(lv['stop'])}, target ~{fmt(lv['target'])}, hold up to {p['hold']} days, ~{sz['shares']} sh. "
                 f"Backtest: {st.get('win', '?')}% win, {st.get('avgR', '?')}R avg over {st.get('n', '?')} trades. Final levels arrive at 9:35 AM.",
                 2 if probation else 4, "test_tube" if probation else "calendar", a.dry)
        # expire stale plans
        for s in state["signals"]:
            if s["status"] == "planned" and (pd.Timestamp(last_date) - pd.Timestamp(s["plan_date"])).days > 4:
                s["status"] = "expired"

    # ---------- 4) Rapid: buy a dip, sell about +0.57% (+$2 on AAPL), hold until green (no stop). Lots are shared across stocks.
    SYMS = CFG.get("rapid_symbols", ["AAPL"])
    RC = dict(lots=CFG.get("rapid_lots", 2), cash=CFG.get("rapid_lot_cash", 35000), aapl_sh=CFG.get("rapid_lot_shares", 100),
              aapl_tgt=CFG.get("rapid_target", 2.0), aapl_str=CFG.get("rapid_stretch", 3.0),
              pct=CFG.get("rapid_target_pct", 0.57) / 100, spct=CFG.get("rapid_stretch_pct", 0.86) / 100, weekly=CFG.get("rapid_weekly_cap", 4))
    R.P["dip"] = CFG.get("rapid_dip", 0.003)
    CTX = {}

    def sctx(sym, need_intraday):
        c = CTX.setdefault(sym, {"df": None, "a5": None, "s5": None, "done_i": False})
        try:
            if c["df"] is None:
                c["df"] = df if sym == "AAPL" else E.build_daily(E.fetch(sym, "2y", "1d"), spy, vix)
            if need_intraday and not c["done_i"]:
                c["done_i"] = True
                c["a5"] = a5 if (sym == "AAPL" and a5 is not None) else E.add_vwap(E.fetch(sym, "5d", "5m"))
                c["s5"] = s5 if s5 is not None else E.add_vwap(E.fetch("SPY", "5d", "5m"))
        except Exception as e:
            print("rapid data failed", sym, e)
        return c

    def last_px(c):
        return float(c["a5"].close.iloc[-1]) if c["a5"] is not None and len(c["a5"]) else float(c["df"].close.iloc[-1])

    def sym_earnings_soon(sym):
        ds = CFG.get("earnings", []) if sym == "AAPL" else CFG.get("earnings_other", {}).get(sym, [])
        return any(0 <= (pd.Timestamp(d) - pd.Timestamp(today)).days <= 1 for d in ds)

    try:
        live_ok = (in_session or after_close) and not a.asof
        rapid_open = [x for x in state["signals"] if x["kind"] == "rapid" and x["status"] == "open"]
        # 4a) monitor open lots
        for s in rapid_open:
            sym = s.get("sym", "AAPL"); c = sctx(sym, live_ok)
            if c["df"] is None: continue
            hit = None
            if c["a5"] is not None:
                b = c["a5"][c["a5"].index > pd.Timestamp(s["created"])]
                if len(b) and float(b.high.max()) >= s["target"]: hit = s["target"]
            later = c["df"][c["df"].index > pd.Timestamp(s["entry_date"])]
            if hit is None and len(later) and float(later.high.max()) >= s["target"]: hit = s["target"]
            sh = s["shares"]
            if hit:
                s.update(status="target", exit=round(hit, 2), exit_date=str(today))
                push(state, f"{s['id']}:exit", f"{sym} Rapid: TARGET HIT",
                     f"Your sell order at {fmt(hit)} should have filled: about {(hit - s['entry']) * sh:+,.0f} USD on {sh} sh. Cash is free again (T+1 settle).", 4, "white_check_mark", a.dry)
                continue
            cur = last_px(c) if live_ok else float(c["df"].close.iloc[-1])
            dd = (cur / s["entry"] - 1) * 100
            s["last"] = round(cur, 2); s["pl_pct"] = round(dd, 2)
            s["days_held"] = int((pd.Timestamp(today) - pd.Timestamp(s["entry_date"])).days)
            for lvl in (5, 10, 20):
                if dd <= -lvl:
                    extra = " This is the point to consider a disaster exit." if lvl == 20 else " Plan is hold until green."
                    push(state, f"{s['id']}:dd{lvl}", f"{sym} Rapid: lot is down {lvl}%",
                         f"Bought {fmt(s['entry'])}, now {fmt(cur)} ({dd:.1f}%, about {(cur - s['entry']) * sh:+,.0f} USD).{extra}", 4, "warning", a.dry)
        # 4b) daily summary after the close
        if after_close and not a.asof and rapid_open and mins < 17 * 60 + 30:
            lines = [f"{x.get('sym', 'AAPL')} lot {fmt(x['entry'])}: {x['pl_pct']:+.1f}%, day {x['days_held']}, needs {fmt(x['target'])} (sell order stays open)"
                     for x in rapid_open if "pl_pct" in x]
            if lines:
                push(state, f"rapid-summary-{today}", "Rapid daily check", "\n".join(lines), 2, "memo", a.dry)
        # 4c) new entries (only at the hourly check times the backtest used)
        mark = next((m for m in (11 * 60 + 30, 12 * 60 + 30, 13 * 60 + 30, 14 * 60 + 30, 15 * 60 + 30) if 0 <= mins - m <= 14), None)
        if in_session and mark and not a.asof:
            wk = now.isocalendar()[1]
            for sym in SYMS:
                open_now = [x for x in state["signals"] if x["kind"] == "rapid" and x["status"] == "open"]
                this_week = [x for x in state["signals"] if x["kind"] == "rapid" and pd.Timestamp(x["created"]).isocalendar()[1] == wk]
                if len(open_now) >= RC["lots"] or len(this_week) >= RC["weekly"]: break
                if sym_earnings_soon(sym): continue
                if any(x["kind"] == "rapid" and x.get("sym", "AAPL") == sym and x["plan_date"] == str(today) for x in state["signals"]): continue
                c = sctx(sym, True)
                if c["df"] is None or c["a5"] is None: continue
                cut = now.normalize() + pd.Timedelta(minutes=mark)
                bars = c["a5"][c["a5"].index.date == today]; bars = bars[bars.index + pd.Timedelta(minutes=5) <= cut]
                sp = c["s5"][c["s5"].index.date == today]; sp = sp[sp.index + pd.Timedelta(minutes=5) <= cut]
                dprev = c["df"][c["df"].index.date < today]
                if len(bars) < 2 or len(dprev) < 2: continue
                spy_prev = float(dprev.spy.iloc[-1]); spy_ret = float(sp.close.iloc[-1]) / spy_prev - 1 if len(sp) else 0.0
                det = R.signal(bars, float(dprev.close.iloc[-1]), bool(dprev.up.iloc[-1]), float(dprev.vix.iloc[-1]), spy_ret, 5)
                if not det: continue
                px = last_px(c); e = round(px, 2)
                if sym == "AAPL": sh, tgt, stc = RC["aapl_sh"], round(e + RC["aapl_tgt"], 2), round(e + RC["aapl_str"], 2)
                else: sh, tgt, stc = int(RC["cash"] / px), round(e * (1 + RC["pct"]), 2), round(e * (1 + RC["spct"]), 2)
                if sh < 1: continue
                sid = f"rapid-{sym}-{today}"
                state["signals"].append(dict(id=sid, kind="rapid", setup="rapid", sym=sym, name=f"{sym} Rapid dip buy", created=now.isoformat(), plan_date=str(today),
                                             entry=e, target=tgt, stretch=stc, shares=sh, status="open", entry_date=str(today),
                                             day_high=det["day_high"], dip_pct=det["dip_pct"], tier="rapid"))
                push(state, f"{sid}:enter", f"{sym} Rapid: BUY the dip (Medium confidence)",
                     f"Buy {sh} sh of {sym} near {fmt(e)} (limit {fmt(e + max(0.10, e * 0.0003))}). Then place a GOOD-TIL-CANCELLED sell limit at {fmt(tgt)} (stretch {fmt(stc)}). "
                     f"Down {det['dip_pct']}% from today's high {fmt(det['day_high'])}. No stop: if it drops, hold until green. Cash used about {sh * e:,.0f} USD. Lots open after this: {len(open_now) + 1} of {RC['lots']}.",
                     5, "rotating_light", a.dry)
    except Exception as e:
        print("rapid error", e)

    # ---------- 5) indicator rule library: status, combo/rule alerts, chart data
    try:
        rtab = load_json(os.path.join(ROOT, "docs", "rules.json"), {})
        rsg = RL.evaluate(df)
        meta = {r["key"]: r for r in rtab.get("rules", [])}
        rule_state = [{"key": k, "name": RL.RULES[k][0], "desc": RL.RULES[k][1], "tier": meta.get(k, {}).get("tier", "weak"),
                       "fired": bool(rsg[k].iloc[-1])} for k in RL.RULES]
        state["rules"] = rule_state; state["rules_tab"] = {"baseline": rtab.get("baseline"), "combos": rtab.get("combos", []), "rules": rtab.get("rules", [])}
        if after_close:
            names = CFG.get("rule_alerts", [])
            hit = [k for k in names if k in rsg and bool(rsg[k].iloc[-1])]
            if hit and not earnings_soon:
                entry = float(d0.close); atr = float(d0.atr)
                stp, tgt = entry - 1.5 * atr, entry + 2.0 * atr
                nm = " + ".join(RL.RULES[k][0] for k in hit)
                push(state, f"rules-{last_date}", "AAPL indicator alert (Medium confidence, half size)",
                     f"{nm}. Idea: buy near tomorrow's open (about {fmt(entry)}), stop {fmt(stp)}, target {fmt(tgt)}, hold up to 5 days. Half size. Medium confidence: these rules beat random entries on AAPL in testing, but with less evidence than the top setup.",
                     3, "bar_chart", a.dry)
    except Exception as e:
        print("rules error", e)
    try:
        d_ = RL.add_ind(df).tail(130)
        def r2(x): return None if pd.isna(x) else round(float(x), 2)
        daily = [[str(i.date())] + [r2(r[c]) for c in ("open", "high", "low", "close", "sma20", "sma50", "sma200", "bb_up", "bb_lo", "rsi14", "macd_hist")] for i, r in d_.iterrows()]
        chart = {"daily": daily, "cols": ["t", "o", "h", "l", "c", "sma20", "sma50", "sma200", "bb_up", "bb_lo", "rsi", "macd_hist"], "updated": now.isoformat()}
        try:
            hh = E.fetch("AAPL", "1mo", "1h"); hh["rsi14"] = E.rsi(hh.close, 14); hh["sma20"] = hh.close.rolling(20).mean(); hh["sma50"] = hh.close.rolling(50).mean()
            sd = hh.close.rolling(20).std(); hh["bb_up"] = hh.sma20 + 2 * sd; hh["bb_lo"] = hh.sma20 - 2 * sd; hh = hh.tail(110)
            chart["hourly"] = [[i.strftime("%m-%d %H:%M"), r2(r.open), r2(r.high), r2(r.low), r2(r.close), r2(r.sma20), r2(r.sma50), None, r2(r.bb_up), r2(r.bb_lo), r2(r.rsi14), None] for i, r in hh.iterrows()]
        except Exception as e:
            print("hourly chart failed", e)
        json.dump(chart, open(os.path.join(os.path.dirname(a.state), "chart.json"), "w"), separators=(",", ":"))
    except Exception as e:
        print("chart error", e)

    # ---------- state for the dashboard
    c = df.close
    def pct(x): return round(float(x) * 100, 2)
    sgn = E.signals(df)
    up = bool(d0.up)
    def clamp(x): return max(0.0, min(1.0, float(x)))
    ibs = float(d0.ibs) if pd.notna(d0.ibs) else 0.5
    W = {
        "gap_down": (f"Today's gap {pct(d0.gap):+.2f}% (needs below -1.5%) in an uptrend" if up else "Needs an uptrend (above 200-day, 50 over 200)", clamp(-d0.gap / 0.015) if up else 0),
        "high52": (f"Price {fmt(d0.close)} vs 52-week high {fmt(d0.hh252)}", clamp((c.iloc[-1] / d0.hh252 - 0.93) / 0.07) if up else 0),
        "oversold": (f"RSI14 is {d0.rsi14:.0f}; triggers below 30", clamp((70 - d0.rsi14) / 40)),
        "breakout": (f"Price {fmt(d0.close)} vs 20-day high {fmt(d0.hh20)}; volume {d0.vratio:.1f}x avg (needs 1.5x)", clamp((c.iloc[-1] / d0.hh20 - 0.95) / 0.05) if up else 0),
        "vix_fall": (f"VIX {d0.vix:.1f}; 5-day high {d0.vix5max:.1f} (needs 25+ then a 10% fall)", clamp((d0.vix5max - 15) / 10) * 0.5 + clamp((1 - d0.vix / d0.vix5max) / 0.1) * 0.5 if up else 0),
        "rsi40": (f"RSI14 is {d0.rsi14:.0f}; triggers below 40 in an uptrend", clamp((70 - d0.rsi14) / 30) if up else 0),
        "drop3": (f"Today {pct(d0.ret):+.2f}% (needs -3% or worse) in an uptrend", clamp(-d0.ret / 0.03) if up else 0),
        "ibs10": (f"Closed at {ibs * 100:.0f}% of today's range (needs under 10%)", clamp((1 - ibs) / 0.9) if ibs > 0.1 else 1),
    }
    watch = [{"key": k, "name": E.SETUPS[k]["name"], "tier": E.SETUPS[k]["tier"], "conf": E.SETUPS[k]["conf"],
              "text": W[k][0], "progress": W[k][1], "fired": bool(sgn[k].iloc[-1])} for k in E.SETUPS]
    closed = [s for s in state["signals"] if s["kind"] == "swing" and s["status"] in ("target", "stop", "timeout")]
    state.update(
        updated=now.isoformat(), session="open" if in_session else "closed", price=round(price, 2), prev_close=round(prev_close, 2),
        chg_pct=round((price / prev_close - 1) * 100, 2), vix=round(float(d0.vix), 2), regime=reg, events=ev,
        trend={"up": bool(d0.up), "sma50": round(float(d0.sma50), 2), "sma200": round(float(d0.sma200), 2),
               "above200": bool(c.iloc[-1] > d0.sma200)},
        ind={"rsi14": round(float(d0.rsi14), 1), "atr": round(float(d0.atr), 2), "atr_pct": round(float(d0.atr / d0.close * 100), 2),
             "hh20": round(float(d0.hh20), 2), "ll20": round(float(d0.ll20), 2), "vratio": round(float(d0.vratio), 2), "bar_date": str(last_date)},
        watch=watch,
        rapid={"lots": RC["lots"], "cash": RC["cash"], "symbols": SYMS, "open": [x for x in state["signals"] if x["kind"] == "rapid" and x["status"] == "open"],
               "closed": [x for x in state["signals"] if x["kind"] == "rapid" and x["status"] == "target"][-10:], "stats": load_json(os.path.join(ROOT, "docs", "rapid_stats.json"), {})},
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
