# DailyTrade

A Windows desktop app (Python + Tkinter) that watches a Futu/Moomoo custom
watchlist for premarket "quiet stock" breakout setups and, when one triggers,
sends a native push alert into the Moomoo app and moves that stock to the top
of the list.

## Files

- `monitor_engine.py` — all backend logic (no UI). Connects to Futu OpenD,
  reads watchlists, evaluates rules, runs the live monitoring loop.
- `DailyTrade_app.py` — the Tkinter GUI. Run with `python DailyTrade_app.py`.
- `DailyTrade.py` — thin CLI fallback around the same engine (not the primary
  interface; the GUI is).
- `DailyTrade.spec` — PyInstaller build spec (see Packaging below).
- `test_connection.py`, `list_watchlists.py`, `test_price_reminder.py` — small
  standalone diagnostic scripts kept around for manually checking OpenD
  connectivity / watchlist contents / price-reminder pushes.
- `DailyTrade_YYYYMMDD.log` — verbose engineering/debug log (connects,
  warnings, per-stock amplitude numbers). Not shown in the GUI.
- `alerts_YYYYMMDD.log` — compact machine-readable trigger log, pipe-delimited
  `timestamp|symbol|rule_name`. This is what the GUI's "Logs" panel reads and
  renders as a table.
- `DailyTrade_config.json` — persisted UI state (selected watchlist, rule
  names/enabled flags), written next to the exe/script.

## Runtime dependency: Futu OpenD

Nothing works without Futu's **OpenD** gateway running and logged in on
`127.0.0.1:11111` — the Python SDK (`futu-api`) never talks to Futu's servers
directly, only to this local gateway. If OpenD isn't up, `monitor_engine.connect()`
retries for up to 5 minutes then raises `EngineError`. Note: if OpenD is not
running, connection attempts (including quick diagnostic scripts) can hang
rather than fail fast — always confirm `Test-NetConnection 127.0.0.1 -Port 11111`
succeeds before debugging "hangs."

## How the app works

1. On launch, connects to OpenD and populates a dropdown with the user's
   **custom** watchlists only (`get_custom_groups` filters out system lists
   like Favorites/US/HK).
2. Selecting a list loads its members into the top table, showing the bare
   ticker **symbol** (e.g. `AAPL`, via `to_symbol()` which strips the
   `US.`/`HK.` market prefix) and the list name — not the company name, not
   the full code.
3. Clicking "Start Daily Monitor" runs `run_daily_cycle`:
   - waits for 9:30 AM ET if started early,
   - for every stock, fetches one quote + one extended-hours 1-min kline
     covering the widest rule window needed, then slices that same data per
     rule (cheap: one API round trip per stock regardless of rule count),
   - each **Rule** defines a premarket amplitude window + threshold + a live
     trigger reference type (`'vwap'` or `'premarket_high'`),
   - stocks that qualify under at least one rule are subscribed to live
     quotes; a `RuleCrossHandler` watches every (stock, rule) pair
     independently and fires when price crosses the reference value.
4. On trigger: sends a one-shot native Moomoo price-reminder alert (a
   `PRICE_UP` reminder set 1 cent below current price, so it fires almost
   immediately — this is the only way found to push a native alert into the
   Moomoo app), moves the stock to the top of its watchlist, appends a line
   to `alerts_YYYYMMDD.log`, and spawns a background thread
   (`_cleanup_reminder_after_fire`) that polls `get_price_reminder` every
   second for up to 30s waiting for that specific reminder's `enable` flag to
   flip to `False` (meaning Moomoo auto-disabled it after firing), then
   deletes it via `SetPriceReminderOp.DEL` so it doesn't linger as a disabled
   entry in the Moomoo app. Falls back to deleting after the 30s timeout
   regardless, so it never lingers indefinitely even if `enable` doesn't flip.
5. Rules can be renamed (persisted) and toggled on/off live; disabling a rule
   still tracks candidates but suppresses alerts for it.

## Current rules

- **Rule 1: VWAP Cross** — premarket window 8:00–9:30 AM ET, amplitude
  `(high-low)/prev_close` must be `< 1.5%`. Live trigger: price crosses above
  the running session VWAP (`cumulative turnover / cumulative volume` since
  the 9:30 AM open, read directly from the live quote's `turnover`/`volume`
  fields — these reset at the regular session open, confirmed empirically).
- **Rule 2: Premarket High Cross** — premarket window 7:00–9:30 AM ET,
  amplitude threshold `< 3%`. Live trigger: price crosses above the fixed
  high recorded during that premarket window (not live-recomputed).

## Non-obvious gotchas (learned the hard way)

- **`get_stock_quote` requires `subscribe(..., subscribe_push=False)` first**
  for those codes, every time, or it errors with "please subscribe to Basic
  data first." Easy to forget when refactoring.
- **"Move to Top" is not a real Futu API operation.** Checked the actual
  protobuf (`Qot_ModifyUserSecurity.proto`): `ModifyUserSecurityOp` only has
  `Add`/`Del`/`MoveOut`. Confirmed empirically that `MOVE_OUT` then `ADD`
  reinserts the stock at position 1 — that's the only way to replicate the
  Moomoo app's own "Move to Top" menu action.
- **Deleting a price reminder right after creating it is a race.** A ONCE
  reminder doesn't necessarily fire the instant it's created — it only fires
  on the next tick Futu's server evaluates, which during quiet/off-hours
  periods can take much longer than a couple seconds (confirmed empirically:
  `enable` was still `True` 2s after creation while the market was quiet).
  Don't delete on a short fixed delay; poll `get_price_reminder`'s `enable`
  field until it flips `False` (see `_cleanup_reminder_after_fire`), with a
  generous timeout fallback.
- **Amplitude base is `prev_close_price` from the live quote**, not any
  `last_close` column in kline data (that column is NOT a stable prior-day
  close — verified it drifts through the day, don't use it).
- **PyInstaller packaging of `futu-api` needs two manual fixes**, both in
  `DailyTrade.spec`:
  1. `futu/common/pb/*.py` (the generated `_pb2.py` files) bare-import each
     other via `sys.path.append(os.path.dirname(__file__))` in
     `futu/common/pb/__init__.py`. That only works if those files are real
     files on disk, so the spec forces them into `datas` instead of letting
     PyInstaller zip them into the PYZ archive.
  2. `futu/__init__.py` reads its own `futu/VERSION.txt` at import time —
     also added to `datas`.
- **`--onefile` builds hung indefinitely on launch** (process alive, 0% CPU,
  memory never grew, no window, no crash) — almost certainly Windows
  Defender doing a synchronous scan on a large (~86MB), fresh, unsigned GUI
  exe. Fixed by switching to **`--onedir`** (files sit on disk permanently,
  no self-extraction per launch) and excluding `matplotlib`/`scipy`/`PIL`/
  `openpyxl`/Qt bindings — all pulled in transitively by pandas' PyInstaller
  hook but never actually used here.
- **When frozen, `__file__` resolves to a temp extraction path**, not the
  exe's real location. Both `DailyTrade_app.py` and the log/config paths use
  `sys.executable`'s parent dir instead when `sys.frozen` is set.

## Packaging

```
python -m PyInstaller --noconfirm DailyTrade.spec
```
Output: `dist/DailyTrade/DailyTrade.exe` (double-click to run — keep the
whole `DailyTrade` folder together, don't move just the exe).

After any code change, rebuild with the command above; `dist/` and `build/`
can be safely deleted first if something seems stale (old `HotListMonitor`-
named build output from before the rename should be deleted, not reused).

## Testing approach used in this project

There's no automated test suite. Verification has been done by actually
running the app/exe and using Win32 API calls from PowerShell to screenshot
the live window and simulate clicks (`SetProcessDPIAware` + `GetWindowRect` +
`SetCursorPos`/`mouse_event`) — DPI awareness must be set in *every*
PowerShell process that computes click coordinates or captures a screenshot,
or coordinates end up scaled wrong and clicks miss / screenshots misalign.
This caught several real bugs (a missing `subscribe()` call, a false-positive
alert on the very first tick after subscribing, missing log lines) that
static review didn't. Prefer this over trusting code review alone for UI/live
API changes here.
