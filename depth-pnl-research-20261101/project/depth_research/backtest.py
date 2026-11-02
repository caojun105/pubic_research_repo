import math
import statistics
from .data import NS, SCALE, iso


def walk(levels, quantity):
    """All-or-none visible-depth VWAP. No partial fills or fabricated liquidity."""
    if quantity <= 0:
        raise ValueError('Quantity must be positive')
    remaining = quantity; cash = 0; used = 0
    for p, q in levels:
        take = min(remaining, q)
        if take:
            cash += p * take; remaining -= take; used += 1
        if not remaining:
            return cash / quantity / SCALE, used
    return None


def summarize(trades, equity, rejections, unresolved=None):
    pnls = [r['net_pnl'] for r in trades]
    wins = [x for x in pnls if x > 1e-10]; losses = [x for x in pnls if x < -1e-10]
    gross = sum(r['gross_pnl'] for r in trades)
    fees = sum(r['entry_fee'] + r['exit_fee'] for r in trades)
    turn = sum(r['quantity_btc'] * (r['entry_price'] + r['exit_price']) for r in trades)
    net = sum(pnls)
    assert math.isclose(gross - fees, net, abs_tol=1e-8)
    if not unresolved and equity:
        assert math.isclose(equity[-1]['equity'], net, abs_tol=1e-8), (equity[-1], net)
    return {'trades': len(trades), 'gross_pnl': gross, 'fees': fees, 'net_pnl': net,
            'mean_net_pnl': statistics.mean(pnls) if pnls else None,
            'win_rate': len(wins) / len(pnls) if pnls else None,
            'average_win': statistics.mean(wins) if wins else None,
            'average_loss': statistics.mean(losses) if losses else None,
            'payoff_ratio': statistics.mean(wins) / abs(statistics.mean(losses)) if wins and losses else None,
            'profit_factor': sum(wins) / abs(sum(losses)) if losses else None,
            'flat_trades': sum(abs(x) <= 1e-10 for x in pnls),
            'mtm_max_drawdown': max((r['drawdown'] for r in equity), default=0),
            'mean_hold_seconds': statistics.mean(r['hold_seconds'] for r in trades) if trades else None,
            'max_hold_seconds': max((r['hold_seconds'] for r in trades), default=None),
            'break_even_fee_bps_per_side': gross / turn * 10000 if turn else None,
            'turnover_quote': turn, 'capital_return': None, 'capital': None,
            'rejections': dict(rejections), 'unresolved': unresolved,
            'valid_complete_result': unresolved is None,
            'terminal_mtm': equity[-1]['equity'] if equity else 0,
            'reconciliation_residual': gross - fees - net}


def simulate(states, start, end, params, qty, latency_ms=100, fee_bps=0,
             max_hold_s=30, entry_sustain_ms=500, exit_sustain_ms=300,
             close_reserve_s=2, benchmark=False, signal_key=None):
    import collections
    latency = round(latency_ms * 1e6)
    close_time = end - round(close_reserve_s * NS)
    entry_cutoff = close_time - round(max_hold_s * NS) - latency
    rows = []; curve = []; rejected = collections.Counter()
    pos = None; order = None; in_since = None; out_since = None
    realized = 0.; peak = 0.; last_mark = None; unresolved = None; seen_segment = None
    attempted_benchmark = False
    for r in states:
        t = r['t']
        if not start <= t < end:
            continue
        if not r['valid'] or (seen_segment is not None and seen_segment != r['segment']):
            in_since = out_since = None
            if order:
                rejected['cancelled_on_invalid_book'] += 1; order = None
            if pos:
                unresolved = {'reason': 'book_discontinuity_with_position', 'entry': pos, 'at_mono_ns': t}
                break
            seen_segment = None
            continue
        seen_segment = r['segment']
        # Fill only on an event strictly later than the signal event, after the configured delay.
        filled_this_event = False
        if order and t > order['signal_t'] and t >= order['due']:
            if order['side'] == 'buy' and t >= entry_cutoff:
                rejected['entry_after_cutoff'] += 1; order = None
            else:
                got = walk(r['asks'] if order['side'] == 'buy' else r['bids'], qty)
                if not got:
                    rejected['insufficient_depth_' + order['side']] += 1
                    order = None
                elif order['side'] == 'buy':
                    px, levels = got
                    pos = {'price': px, 't': t, 'utc': r['utc'], 'fee': px * qty / SCALE * fee_bps / 10000,
                           'signal_t': order['signal_t'], 'signal_utc': order['signal_utc'], 'levels': levels}
                    order = None; in_since = out_since = None; filled_this_event = True
                else:
                    px, levels = got; q = qty / SCALE
                    gross = (px - pos['price']) * q; fee_out = px * q * fee_bps / 10000
                    net = gross - pos['fee'] - fee_out
                    rows.append({'direction': 'long', 'quantity_btc': q,
                                 'entry_signal_mono_ns': pos['signal_t'], 'exit_signal_mono_ns': order['signal_t'],
                                 'entry_signal_utc': iso(pos['signal_utc']), 'exit_signal_utc': iso(order['signal_utc']),
                                 'entry_mono_ns': pos['t'], 'exit_mono_ns': t,
                                 'entry_utc': iso(pos['utc']), 'exit_utc': iso(r['utc']),
                                 'entry_price': pos['price'], 'exit_price': px,
                                 'entry_levels': pos['levels'], 'exit_levels': levels,
                                 'entry_effective_delay_ms': (pos['t'] - pos['signal_t']) / 1e6,
                                 'exit_effective_delay_ms': (t - order['signal_t']) / 1e6,
                                 'exit_reason': order['reason'], 'hold_seconds': (t - pos['t']) / NS,
                                 'gross_pnl': gross, 'entry_fee': pos['fee'], 'exit_fee': fee_out, 'net_pnl': net})
                    realized += net; pos = None; order = None
                    in_since = out_since = None; filled_this_event = True
        if pos:
            # Mid-marked equity includes incurred entry fee; exit fee is charged on actual sale.
            mark = realized + (r['mid'] - pos['price']) * qty / SCALE - pos['fee']
        else:
            mark = realized
        last_mark = mark; peak = max(peak, mark)
        curve.append({'mono_ns': t, 'utc': iso(r['utc']), 'equity': mark, 'drawdown': peak - mark,
                      'realized_net': realized, 'position_btc': qty / SCALE if pos else 0})
        if order or filled_this_event:
            continue
        reason = None
        if pos:
            if t >= close_time:
                reason = 'scheduled_boundary_exit'
            elif not benchmark and t - pos['t'] >= max_hold_s * NS:
                reason = 'max_hold'
            elif not benchmark:
                if signal_key is None:
                    weak = r['bid1'] < params['bid1_min'] or (r['A'] > 0 and r['B'] <= params['ratio_out'] * r['A'])
                else:
                    weak = r[signal_key] is None or r[signal_key] <= params['exit']
                if weak:
                    if out_since is None:
                        out_since = t
                    if t - out_since >= exit_sustain_ms * 1e6:
                        reason = 'imbalance_weak'
                else:
                    out_since = None
            if reason:
                order = {'side': 'sell', 'due': t + latency, 'signal_t': t, 'signal_utc': r['utc'], 'reason': reason}
        elif t < entry_cutoff:
            if signal_key is None:
                ok = r['bid1'] >= params['bid1_min'] and r['A'] > 0 and r['B'] >= params['ratio_in'] * r['A']
            else:
                ok = r[signal_key] is not None and r[signal_key] >= params['entry']
            if benchmark:
                ok = not attempted_benchmark
            if ok:
                if in_since is None:
                    in_since = t
                if benchmark or t - in_since >= entry_sustain_ms * 1e6:
                    order = {'side': 'buy', 'due': t + latency, 'signal_t': t, 'signal_utc': r['utc']}
                    attempted_benchmark = True; in_since = None
            else:
                in_since = None
    if pos and unresolved is None:
        unresolved = {'reason': 'no_executable_exit_before_boundary', 'entry': pos, 'last_mtm': last_mark}
    if order and order['side'] == 'buy':
        rejected['pending_entry_expired'] += 1
    return {'summary': summarize(rows, curve, rejected, unresolved), 'trades': rows, 'equity': curve}
