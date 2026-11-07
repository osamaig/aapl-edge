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
