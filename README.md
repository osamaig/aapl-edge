# AAPL Edge

Watches AAPL, finds the 3 setups that held up in a 15-year backtest, and pushes alerts to your phone with entry, stop, target and share count.

## One-time setup (about 15 minutes)

1. **Phone alerts.** Install the free **ntfy** app. Tap "+", subscribe to a topic name nobody could guess (the one Claude gave you). That name is your password.
2. **GitHub.** Create a new **public** repository (public makes the free automation and free web page work). Upload this whole folder to it.
3. **Secrets.** In the repo: Settings > Secrets and variables > Actions > New secret. Add:
   - `NTFY_TOPIC` = your ntfy topic name
   - `CAPITAL` = your cash amount (kept private)
   - `RISK_PCT` = 0.5
4. **Turn on the web page.** Settings > Pages > Source: "Deploy from a branch", branch `main`, folder `/docs`. Your dashboard appears at `https://YOURNAME.github.io/REPONAME/`. Add it to your phone's home screen.
5. **Start the scanner.** Actions tab > "AAPL scan" > Run workflow. After that it runs by itself every 5 minutes on weekdays.

## Setup tiers
- **Core (full size):** Gap-down dip buy, 52-week high breakout (High confidence); Oversold bounce, Volume breakout (Moderate).
- **Probation (half size, unproven):** VIX falling from spike, RSI under 40 pullback, Drop over 3%, Closed at day low (IBS). Tracked live; they only graduate to core after real results hold up.

## What you get
- **After the close:** "plan for tomorrow" alert when a setup fires.
- **9:35 AM next day:** "ENTER NOW" with real entry, stop, target, shares.
- **Any time:** stop hit, target hit, or time stop alerts.
- **Paper scalps:** logged and pushed at low priority. Do not trade them until the scorecard shows real results.

## Rules built in
- 0.5% of cash risked per trade, max 25% of cash in one position, max 2 open trades.
- Size cut to 75% when VIX is 22+, 50% (and breakout paused) at VIX 30+.
- New entries pause the day before earnings. Put dates in `config.json` ("earnings": ["2027-01-29"]). FOMC dates are listed there too.
- Cash account: sale proceeds settle the next business day. Do not reuse them the same day.

## Honest limits
- Data is free and unofficial (Yahoo). It can lag or fail; the dashboard warns when the scan is late.
- GitHub's 5-minute schedule can run several minutes late. Fine for swing trades, unreliable for scalping.
- Backtest sizes are small (7 to 85 trades per setup). Treat results as evidence, not proof.
- Refresh the backtest any time: Actions > Run workflow (it re-runs `scanner/backtest.py`).

## AAPL Rapid
Buy a small dip (0.3% off the day's high, uptrend, calm market), sell +$2 per share, no stop, hold until green. Up to 2 lots of 100 shares. Alerts at -5% and -10%, a daily check after the close, max 3 alerts a week, paused around earnings. Settings in config.json (rapid_*). Backtest in docs/rapid_stats.json.

## Indicator rules + chart
scanner/rules.py holds 20 standard analyst rules (RSI, MACD, moving averages, Bollinger, stochastic, volume, breakouts, candles). scanner/rules_backtest.py tests each against random entries (train/test split, slow-day check, 12 other stocks) and writes docs/rules.json. Only rules that pass become "proven"; "AAPL-only" ones get half-size alerts via config.json `rule_alerts`. The dashboard shows a candlestick chart (daily/hourly) with overlays and RSI, plus a rule status panel.
