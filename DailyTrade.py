"""
CLI entry point for the multi-rule watchlist breakout monitor (see
monitor_engine.py for the actual logic). Prefer running DailyTrade_app.py
for the GUI version.

Usage: python DailyTrade.py [WatchlistName]
If no watchlist name is given, the first custom watchlist found is used.

Run any time before market open; it waits for 9:30 AM ET on its own.
Stop with Ctrl+C.
"""

import sys
import threading

import monitor_engine as engine


def main():
    engine.configure_logging('.')
    log = engine.log
    log.addHandler(_console_handler())

    stop_event = threading.Event()
    rules = engine.default_rules()

    try:
        ctx = engine.connect(poll_cb=log.warning)
    except engine.EngineError as e:
        log.error(str(e))
        sys.exit(1)

    try:
        group_name = sys.argv[1] if len(sys.argv) > 1 else None
        if not group_name:
            groups = engine.get_custom_groups(ctx)
            if not groups:
                log.error('No custom watchlists found in Moomoo.')
                sys.exit(1)
            group_name = groups[0]
            log.info('No watchlist given, using "%s".', group_name)

        engine.run_daily_cycle(
            ctx, group_name, rules, stop_event,
            candidates_cb=lambda rows: None,
            status_cb=log.info,
            alert_cb=lambda code, rule_name, price, ref: None,
        )
    except KeyboardInterrupt:
        stop_event.set()
        log.info('Stopped by user.')
    finally:
        ctx.close()


def _console_handler():
    import logging
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(logging.Formatter('%(asctime)s | %(levelname)s | %(message)s'))
    return h


if __name__ == '__main__':
    main()
