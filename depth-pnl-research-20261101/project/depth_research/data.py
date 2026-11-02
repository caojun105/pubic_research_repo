import bisect
import collections
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

SCALE = 100_000_000
NS = 1_000_000_000


def units(x):
    v = Decimal(str(x)) * SCALE
    if v != v.to_integral_value():
        raise ValueError('Precision greater than eight decimals')
    return int(v)


def iso(ns):
    return datetime.fromtimestamp(ns / NS, timezone.utc).isoformat()


def load(path):
    with Path(path).open() as f:
        return [json.loads(line) for line in f if line.strip()]


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def timestamp(r):
    if 'available' in r:
        return r['available']
    if 'available_mono_ns' in r:
        return {'local_mono_ns': r['available_mono_ns'], 'local_utc_ns': r['available_utc_ns']}
    if 'received' in r:
        return r['received']
    if 'aligned_mono_ns' in r:
        return {'local_mono_ns': r['aligned_mono_ns'], 'local_utc_ns': r['aligned_utc_ns']}
    if 'local_mono_ns' in r:
        return r
    return None


def audit(directory):
    result = {'files': {}, 'time_basis': 'local monotonic availability; UTC only for display'}
    for p in sorted(Path(directory).glob('*')):
        if not p.is_file():
            continue
        info = {'bytes': p.stat().st_size, 'sha256': sha(p)}
        if p.suffix == '.jsonl':
            rows = []; errors = []
            with p.open() as f:
                for i, line in enumerate(f, 1):
                    try:
                        rows.append(json.loads(line))
                    except (ValueError, TypeError) as e:
                        errors.append({'line': i, 'error': str(e)})
            ts = [timestamp(r) for r in rows if timestamp(r)]
            info.update(rows=len(rows), parse_errors=errors)
            if ts:
                for key in ('local_mono_ns', 'local_utc_ns'):
                    values = [t[key] for t in ts]
                    info[key] = {'first': values[0], 'last': values[-1], 'min': min(values), 'max': max(values),
                                 'backwards': sum(b < a for a, b in zip(values, values[1:])),
                                 'ties': sum(b == a for a, b in zip(values, values[1:]))}
                info['start_utc'] = iso(min(t['local_utc_ns'] for t in ts))
                info['end_utc'] = iso(max(t['local_utc_ns'] for t in ts))
            if p.name == 'binance_raw.jsonl':
                depths = [r for r in rows if r['data']['e'] == 'depthUpdate']
                info['symbols'] = dict(collections.Counter(r['data']['s'] for r in rows))
                info['streams'] = dict(collections.Counter(r['stream'] for r in rows))
                info['sessions'] = dict(collections.Counter(str(r['session']) for r in rows))
                info['max_depth_receive_gap_ms'] = max((b['received']['local_mono_ns'] - a['received']['local_mono_ns']) / 1e6 for a, b in zip(depths, depths[1:]))
                info['exchange_event_backwards'] = sum(b['data']['E'] < a['data']['E'] for a, b in zip(depths, depths[1:]))
                offsets = [r['received']['local_utc_ns'] / 1e6 - r['data']['E'] for r in depths]
                info['receive_minus_exchange_ms_range'] = [min(offsets), max(offsets)]
            if p.name == 'var_quotes.jsonl':
                info['http_status'] = dict(collections.Counter(str(r.get('http_status')) for r in rows))
                info['valid_rows'] = sum(r.get('valid', False) for r in rows)
            if p.name == 'aligned_1s.jsonl':
                info['binance_status'] = dict(collections.Counter(r['binance']['status'] for r in rows))
                info['sample_sequence_errors'] = sum(b['sample'] != a['sample'] + 1 for a, b in zip(rows, rows[1:]))
                info['grid_gap_errors'] = sum(b['aligned_mono_ns'] - a['aligned_mono_ns'] != NS for a, b in zip(rows, rows[1:]))
            if p.name == 'clock_probes.jsonl':
                info['offset_bounds_ms'] = [min(r['server_minus_local_lower_ms'] for r in rows), max(r['server_minus_local_upper_ms'] for r in rows)]
                info['rtt_ms_range'] = [min(r['rtt_ms'] for r in rows), max(r['rtt_ms'] for r in rows)]
        result['files'][p.name] = info
    return result


class Book:
    """Track only the snapshot's provably complete price interval."""
    def __init__(self, snapshot):
        self.b = {units(p): units(q) for p, q in snapshot['bids'] if units(q) > 0}
        self.a = {units(p): units(q) for p, q in snapshot['asks'] if units(q) > 0}
        self.floor, self.ceiling = min(self.b), max(self.a)
        self.bp, self.ap = sorted(self.b), sorted(self.a)
        self.uid = snapshot['lastUpdateId']

    def update(self, event):
        for key, side, prices in [('b', self.b, self.bp), ('a', self.a, self.ap)]:
            for p, q in event[key]:
                p, q = units(p), units(q)
                if not self.floor <= p <= self.ceiling:
                    continue
                if q < 0:
                    raise ValueError('Negative depth')
                if not q:
                    if p in side:
                        del side[p]
                        prices.pop(bisect.bisect_left(prices, p))
                else:
                    if p not in side:
                        bisect.insort(prices, p)
                    side[p] = q
        self.uid = event['u']

    def state(self, t, utc, segment, qty):
        if not self.bp or not self.ap or self.bp[-1] >= self.ap[0]:
            return None
        mid2 = self.bp[-1] + self.ap[0]
        covered = 2 * self.floor <= mid2 - 12 * SCALE and 2 * self.ceiling >= mid2 + 12 * SCALE
        bands = []
        for side, prices in [(self.b, self.bp), (self.a, self.ap)]:
            sums = [0] * 6
            lo = bisect.bisect_left(prices, (mid2 - 12 * SCALE) // 2)
            hi = bisect.bisect_right(prices, (mid2 + 12 * SCALE) // 2)
            for p in prices[lo:hi]:
                j = abs(2 * p - mid2) // (2 * SCALE)
                if j < 6:
                    sums[j] += side[p]
            bands.append([q / SCALE for q in sums])
        def enough(side, prices):
            levels = []; total = 0
            for p in prices:
                levels.append((p, side[p])); total += side[p]
                if total >= qty:
                    break
            return levels
        return {'t': t, 'utc': utc, 'segment': segment, 'valid': covered, 'uid': self.uid,
                'mid': mid2 / (2 * SCALE), 'bid': self.bp[-1] / SCALE, 'ask': self.ap[0] / SCALE,
                'bid1': self.b[self.bp[-1]] / SCALE, 'bbands': bands[0], 'abands': bands[1],
                'B': sum(bands[0]), 'A': sum(bands[1]),
                'bids': enough(self.b, reversed(self.bp)), 'asks': enough(self.a, iter(self.ap))}


def replay(snapshots, raw, qty, max_gap_ms=1000):
    # File order is audited before sorting. Stable arrival ordering, never exchange timestamps.
    events = [(timestamp(r)['local_mono_ns'], 0, i, r) for i, r in enumerate(snapshots)]
    events += [(r['received']['local_mono_ns'], 1, i, r) for i, r in enumerate(raw) if r['data']['e'] == 'depthUpdate']
    events.sort(key=lambda x: x[:3])
    counts = collections.Counter({k: 0 for k in ['stale', 'sequence_gap', 'timeout', 'session_mismatch', 'invalid_book', 'uncovered', 'snapshots', 'resyncs', 'ignored_unsynced', 'buffered', 'bridges']})
    states = []; book = None; live = False; blocked = False; session = None; segment = 0; last_t = None; pending = []

    def invalid(t, utc, reason):
        nonlocal live, blocked
        live = False; blocked = True
        counts[reason] += 1
        states.append({'t': t, 'utc': utc, 'valid': False, 'segment': segment, 'reason': reason})

    def apply(r, t, utc):
        nonlocal live, last_t
        e = r['data']
        if r['session'] != session:
            invalid(t, utc, 'session_mismatch'); return
        if live and last_t is not None and t - last_t > max_gap_ms * 1e6:
            invalid(t, utc, 'timeout'); return
        if e['u'] <= book.uid:
            counts['stale'] += 1; return
        if e['U'] > book.uid + 1 or e['U'] > e['u']:
            invalid(t, utc, 'sequence_gap'); return
        if not live:
            counts['bridges'] += 1
        book.update(e); live = True; last_t = t

    for t, typ, _, r in events:
        utc = timestamp(r)['local_utc_ns']
        if typ == 0:
            counts['snapshots'] += 1
            if book is not None:
                counts['resyncs'] += 1
                states.append({'t': t, 'utc': utc, 'valid': False, 'segment': segment, 'reason': 'new_snapshot'})
            book = Book(r['snapshot']); session = r['session']; segment += 1
            live = False; blocked = False; last_t = None
            for old in pending:
                if old['session'] == session and not blocked:
                    apply(old, t, utc)
            pending = []
        elif book is None:
            pending.append(r); counts['buffered'] += 1; continue
        elif blocked:
            counts['ignored_unsynced'] += 1; continue
        else:
            previous_uid = book.uid
            apply(r, t, utc)
            if live and book.uid == previous_uid:
                continue
        if live:
            state = book.state(t, utc, segment, qty)
            if state is None:
                invalid(t, utc, 'invalid_book')
            elif not state['valid']:
                invalid(t, utc, 'uncovered')
            else:
                states.append(state)
    return states, dict(counts)
