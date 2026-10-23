"""
Backend engine for the multi-rule watchlist breakout monitor.

Pure logic, no UI. Callers (the GUI, or the CLI fallback in DailyTrade.py)
drive it via callbacks and a threading.Event so a run can be started and
stopped interactively instead of being a single blocking script.
"""

import os
import time
import logging
import threading
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

from futu import (
    OpenQuoteContext, RET_OK,
    SubType, KLType, AuType, StockQuoteHandlerBase,
    SetPriceReminderOp, PriceReminderType, PriceReminderFreq, PriceReminderMarketStatus,
    ModifyUserSecurityOp,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
MARKET_OPEN = dtime(9, 30)
MARKET_CLOSE = dtime(16, 0)
OPEND_HOST = '127.0.0.1'
OPEND_PORT = 11111
ALERT_ONCE_PER_DAY = True

ET = ZoneInfo('America/New_York')

log = logging.getLogger('DailyTrade')

_ALERTS_DIR = '.'


class EngineError(Exception):
    pass


class Rule:
    """A breakout rule: a premarket 'quiet stock' filter (amplitude window +
    threshold) plus a live trigger condition checked once the market opens.

    reference is either 'vwap' (checked live every tick as
    cumulative turnover / cumulative volume since the 9:30 open) or
    'premarket_high' (a fixed number: the high of the premarket window)."""

    def __init__(self, rule_id, name, description, window_start, window_end,
                 amp_threshold_pct, reference, enabled=True):
        self.id = rule_id
        self.name = name
        self.description = description
        self.window_start = window_start
        self.window_end = window_end
        self.amp_threshold_pct = amp_threshold_pct
        self.reference = reference
        self.enabled = enabled


def default_rules():
    return [
        Rule(
            'rule1', 'Rule 1: VWAP Cross',
            'After the pre-market amplitude filter keeps only "quiet" stocks '
            '(high-low range under 1.5% of previous close, measured 8:00-9:30 AM ET), '
            'this rule watches each candidate\'s live price against its running session '
            'VWAP (cumulative turnover ÷ cumulative volume since the 9:30 AM open). '
            'The instant price crosses from at/below VWAP to above it, this rule triggers.',
            dtime(8, 0), dtime(9, 30), 1.5, 'vwap', True,
        ),
        Rule(
            'rule2', 'Rule 2: Premarket High Cross',
            'Keeps stocks whose premarket high-low range is under 3% of previous close, '
            'measured 7:00-9:30 AM ET, and remembers each one\'s premarket high price. '
            'Once the market opens, the instant live price crosses above that premarket '
            'high, this rule triggers.',
            dtime(7, 0), dtime(9, 30), 3.0, 'premarket_high', True,
        ),
    ]


def configure_logging(directory='.'):
    global _ALERTS_DIR
    _ALERTS_DIR = directory
    day = now_et().strftime('%Y%m%d')
    handler = logging.FileHandler(f'{directory}/DailyTrade_{day}.log', encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s | %(levelname)s | %(message)s'))
    log.setLevel(logging.INFO)
    log.addHandler(handler)
    log.propagate = False


def now_et():
    return datetime.now(ET)


def to_symbol(code):
    """'US.AAPL' -> 'AAPL'"""
    return code.split('.', 1)[1] if '.' in code else code


def alerts_log_path(directory, day=None):
    day = day or now_et().date()
    return os.path.join(directory, f'alerts_{day.strftime("%Y%m%d")}.log')


def log_alert_event(code, rule_name):
    ts = now_et().strftime('%Y-%m-%d %H:%M:%S')
    line = f'{ts}|{to_symbol(code)}|{rule_name}\n'
    try:
        with open(alerts_log_path(_ALERTS_DIR), 'a', encoding='utf-8') as f:
            f.write(line)
    except OSError as e:
        log.error('Could not write alert log: %s', e)


def read_alert_events(directory, day):
    path = alerts_log_path(directory, day)
    events = []
    if not os.path.exists(path):
        return events
    with open(path, encoding='utf-8', errors='replace') as f:
        for line in f:
            parts = line.rstrip('\n').split('|', 2)
            if len(parts) == 3:
                events.append({'time': parts[0], 'symbol': parts[1], 'rule': parts[2]})
    return events


def connect(timeout_seconds=300, poll_cb=None):
    deadline = time.time() + timeout_seconds
    while True:
        try:
            ctx = OpenQuoteContext(host=OPEND_HOST, port=OPEND_PORT)
            log.info('Connected to OpenD at %s:%s', OPEND_HOST, OPEND_PORT)
            return ctx
        except Exception as e:
            if time.time() > deadline:
                raise EngineError(f'Could not connect to OpenD after {timeout_seconds}s: {e}')
            if poll_cb:
                poll_cb(f'OpenD not reachable yet ({e}). Retrying...')
            time.sleep(5)


def get_custom_groups(ctx):
    ret, data = ctx.get_user_security_group()
    if ret != RET_OK:
        raise EngineError(f'Could not read watchlist groups: {data}')
    names = data[data['group_type'] == 'CUSTOM']['group_name'].tolist()
    return names


def get_group_members(ctx, group_name):
    ret, data = ctx.get_user_security(group_name)
    if ret != RET_OK:
        raise EngineError(f'Could not read watchlist "{group_name}": {data}')
    codes = data['code'].tolist()
    log.info('"%s" list (%d stocks): %s', group_name, len(codes), ', '.join(codes) or '(empty)')
    return codes


def move_to_top(ctx, group_name, code):
    ret, res = ctx.modify_user_security(group_name, ModifyUserSecurityOp.MOVE_OUT, [code])
    if ret != RET_OK:
        log.warning('Could not move %s to top of "%s" (remove step failed: %s)', code, group_name, res)
        return False
    ret, res = ctx.modify_user_security(group_name, ModifyUserSecurityOp.ADD, [code])
    if ret != RET_OK:
        log.warning('Could not move %s to top of "%s" (re-add step failed: %s)', code, group_name, res)
        return False
    log.info('Moved %s to top of "%s" list', code, group_name)
    return True


def wait_for_market_open(stop_event, status_cb=None):
    n = now_et()
    open_dt = datetime.combine(n.date(), MARKET_OPEN, tzinfo=ET)
    if n >= open_dt:
        return True
    if status_cb:
        status_cb(f'Waiting for market open at {MARKET_OPEN.strftime("%H:%M")} ET '
                   f'(currently {n.strftime("%H:%M:%S")} ET)...')
    while now_et() < open_dt:
        if stop_event.wait(1):
            return False
    # small buffer so the last premarket bar is fully finalized on the server
    return not stop_event.wait(15)


def _window_high_low(kdata, day, window_start, window_end):
    start_key = f"{day} {window_start.strftime('%H:%M:%S')}"
    end_key = f"{day} {window_end.strftime('%H:%M:%S')}"
    window = kdata[(kdata['time_key'] >= start_key) & (kdata['time_key'] < end_key)]
    if window.empty:
        return None
    return float(window['high'].max()), float(window['low'].min())


def evaluate_stock(ctx, code, rules):
    """Fetch one stock's quote + premarket kline once, then evaluate it
    against every rule's own window/threshold.

    Returns (name, prev_close, {rule_id: {'qualifies': bool, 'amplitude': float,
    'high': float}}) or None if data wasn't available.
    """
    ret, quote = ctx.get_stock_quote([code])
    if ret != RET_OK or quote.empty:
        log.warning('%s: could not fetch quote (%s)', code, quote)
        return None
    name = str(quote.loc[0, 'name'])
    prev_close = float(quote.loc[0, 'prev_close_price'])
    if prev_close <= 0:
        log.warning('%s: invalid prev_close_price=%s', code, prev_close)
        return None

    day = now_et().strftime('%Y-%m-%d')
    ret, kdata, _ = ctx.request_history_kline(
        code, start=day, end=day, ktype=KLType.K_1M, autype=AuType.QFQ,
        extended_time=True, max_count=1000,
    )
    if ret != RET_OK or kdata is None or kdata.empty:
        log.warning('%s: could not fetch premarket kline (%s)', code, kdata)
        return None

    per_rule = {}
    for rule in rules:
        hl = _window_high_low(kdata, day, rule.window_start, rule.window_end)
        if hl is None:
            per_rule[rule.id] = {'qualifies': False, 'amplitude': None, 'high': None}
            continue
        high, low = hl
        amplitude = (high - low) / prev_close * 100
        per_rule[rule.id] = {
            'qualifies': amplitude < rule.amp_threshold_pct,
            'amplitude': amplitude,
            'high': high,
        }
    return name, prev_close, per_rule


REMINDER_POLL_INTERVAL_SECONDS = 1.0
REMINDER_POLL_TIMEOUT_SECONDS = 30.0


def fire_alert(ctx, code, last_price, note):
    """Creates a one-shot Moomoo price-reminder push. Returns the reminder's
    key on success (needed to delete it again afterward), or None on failure."""
    trigger_value = round(last_price - 0.01, 2)
    ret, res = ctx.set_price_reminder(
        code=code,
        op=SetPriceReminderOp.ADD,
        reminder_type=PriceReminderType.PRICE_UP,
        reminder_freq=PriceReminderFreq.ONCE,
        value=trigger_value,
        note=note,
        reminder_session_list=[
            PriceReminderMarketStatus.OPEN,
            PriceReminderMarketStatus.US_PRE,
            PriceReminderMarketStatus.US_AFTER,
            PriceReminderMarketStatus.US_OVERNIGHT,
        ],
    )
    if ret == RET_OK:
        log.info('ALERT SENT to Moomoo: %s at %.2f (%s, reminder key=%s)', code, last_price, note, res)
        return res
    log.error('ALERT for %s FAILED to send (%s) -- %s', code, res, note)
    return None


def delete_reminder(ctx, code, key):
    ret, res = ctx.set_price_reminder(code=code, op=SetPriceReminderOp.DEL, key=key)
    if ret == RET_OK:
        log.info('Deleted one-time reminder for %s (key=%s)', code, key)
    else:
        log.warning('Could not delete reminder for %s (key=%s): %s', code, key, res)
    return ret == RET_OK


def _cleanup_reminder_after_fire(ctx, code, key):
    """Runs in a background thread: waits for the ONCE-frequency reminder we
    just created to actually fire (Moomoo auto-disables it once triggered,
    reflected as enable=False), then deletes it so it doesn't linger in the
    Moomoo app's price-reminder list. Falls back to deleting on a timeout so
    it never lingers indefinitely even if the enable flag doesn't flip."""
    deadline = time.time() + REMINDER_POLL_TIMEOUT_SECONDS
    while time.time() < deadline:
        time.sleep(REMINDER_POLL_INTERVAL_SECONDS)
        ret, data = ctx.get_price_reminder(code=code)
        if ret != RET_OK:
            continue
        match = data[data['key'] == key]
        if match.empty:
            return  # already gone (e.g. user deleted it manually)
        if not bool(match.iloc[0]['enable']):
            break  # fired and auto-disabled
    delete_reminder(ctx, code, key)


class RuleCrossHandler(StockQuoteHandlerBase):
    """candidate_rules: {code: [(rule, fixed_reference_or_None), ...]}
    fixed_reference is the premarket high for 'premarket_high' rules, or
    None for 'vwap' rules (computed live from turnover/volume each tick)."""

    def __init__(self, ctx, group_name, candidate_rules, alert_cb=None):
        super().__init__()
        self.ctx = ctx
        self.group_name = group_name
        self.candidate_rules = candidate_rules
        self.alert_cb = alert_cb
        self.state = {}

    def on_recv_rsp(self, rsp_pb):
        ret, data = super().on_recv_rsp(rsp_pb)
        if ret != RET_OK:
            return ret, data
        for _, row in data.iterrows():
            code = row['code']
            rules_for_code = self.candidate_rules.get(code)
            if not rules_for_code:
                continue
            last_price = float(row.get('last_price') or 0)
            if last_price <= 0:
                continue
            volume = float(row.get('volume') or 0)
            turnover = float(row.get('turnover') or 0)

            for rule, fixed_ref in rules_for_code:
                if fixed_ref is not None:
                    ref_value = fixed_ref
                elif volume > 0:
                    ref_value = turnover / volume
                else:
                    continue

                key = (code, rule.id)
                st = self.state.setdefault(key, {'was_above': None, 'alerted': False})
                is_above = last_price > ref_value

                if st['was_above'] is None:
                    # first tick for this (stock, rule): seed state only
                    st['was_above'] = is_above
                    continue

                if is_above and not st['was_above'] and not (ALERT_ONCE_PER_DAY and st['alerted']):
                    log.info('%s CROSS [%s]: last=%.2f ref=%.2f', code, rule.name, last_price, ref_value)
                    if rule.enabled:
                        reminder_key = fire_alert(self.ctx, code, last_price, rule.name)
                        if reminder_key is not None:
                            st['alerted'] = True
                            move_to_top(self.ctx, self.group_name, code)
                            log_alert_event(code, rule.name)
                            if self.alert_cb:
                                self.alert_cb(code, rule.name, last_price, ref_value)
                            threading.Thread(
                                target=_cleanup_reminder_after_fire,
                                args=(self.ctx, code, reminder_key),
                                daemon=True,
                            ).start()
                    else:
                        log.info('%s crossed [%s] but the rule is disabled - no alert sent', code, rule.name)

                st['was_above'] = is_above
        return RET_OK, data


def run_daily_cycle(ctx, group_name, rules, stop_event, candidates_cb=None, status_cb=None, alert_cb=None):
    """Runs one full day's cycle: wait for open -> evaluate every rule's
    premarket filter -> monitor qualifying candidates until close or stop."""
    try:
        codes = get_group_members(ctx, group_name)
    except EngineError as e:
        if status_cb:
            status_cb(str(e))
        return

    if candidates_cb:
        candidates_cb([{'code': c, 'name': to_symbol(c), 'group_name': group_name, 'is_candidate': False} for c in codes])

    if not codes:
        if status_cb:
            status_cb(f'"{group_name}" list is empty. Nothing to monitor.')
        return

    if status_cb:
        status_cb('Waiting for market open (9:30 AM ET)...')
    if not wait_for_market_open(stop_event, status_cb):
        log.info('Stopped before market open.')
        if status_cb:
            status_cb('Stopped before market open.')
        return

    log.info('Evaluating premarket rules for %d stock(s) in "%s"...', len(codes), group_name)
    if status_cb:
        status_cb('Evaluating premarket rules...')

    ret, err = ctx.subscribe(codes, [SubType.QUOTE], subscribe_push=False)
    if ret != RET_OK:
        log.error('Subscribe (for premarket quotes) failed: %s', err)
        if status_cb:
            status_cb(f'Subscribe failed: {err}')
        return

    rows = []
    candidate_rules = {}  # code -> [(rule, fixed_ref_or_None), ...]
    for code in codes:
        if stop_event.is_set():
            log.info('Stopped.')
            if status_cb:
                status_cb('Stopped.')
            return

        result = evaluate_stock(ctx, code, rules)
        if result is None:
            rows.append({'code': code, 'name': to_symbol(code), 'group_name': group_name, 'is_candidate': False})
            if candidates_cb:
                candidates_cb(list(rows))
            continue

        _name, _prev_close, per_rule = result
        matched = []
        for rule in rules:
            info = per_rule.get(rule.id, {})
            qualifies = info.get('qualifies')
            amp = info.get('amplitude')
            if amp is None:
                log.warning('%s: no data for rule "%s" window', code, rule.name)
                continue
            log.info('%s [%s] premarket amplitude = %.3f%%  -> %s',
                      code, rule.name, amp, 'CANDIDATE' if qualifies else 'skip')
            if qualifies:
                fixed_ref = info['high'] if rule.reference == 'premarket_high' else None
                matched.append((rule, fixed_ref))

        if matched:
            candidate_rules[code] = matched
        rows.append({
            'code': code, 'name': to_symbol(code), 'group_name': group_name,
            'is_candidate': bool(matched),
        })
        if candidates_cb:
            candidates_cb(list(rows))

    n_candidates = len(candidate_rules)
    log.info('Candidates to watch (%d): %s', n_candidates, ', '.join(candidate_rules) or '(none)')
    if not candidate_rules:
        if status_cb:
            status_cb('No candidates qualified today under any rule.')
        return

    handler = RuleCrossHandler(ctx, group_name, candidate_rules, alert_cb=alert_cb)
    ctx.set_handler(handler)
    watch_codes = list(candidate_rules.keys())
    ret, err = ctx.subscribe(watch_codes, [SubType.QUOTE], subscribe_push=True)
    if ret != RET_OK:
        if status_cb:
            status_cb(f'Subscribe failed: {err}')
        return

    log.info('Monitoring %d candidate(s) until 4:00 PM ET.', n_candidates)
    if status_cb:
        status_cb(f'Monitoring {n_candidates} candidate(s) until 4:00 PM ET.')
    close_dt = datetime.combine(now_et().date(), MARKET_CLOSE, tzinfo=ET)
    while now_et() < close_dt:
        if stop_event.wait(1):
            break

    try:
        ctx.unsubscribe(watch_codes, [SubType.QUOTE])
    except Exception:
        pass

    final_msg = 'Stopped.' if stop_event.is_set() else 'Market closed. Monitor finished for today.'
    log.info(final_msg)
    if status_cb:
        status_cb(final_msg)
