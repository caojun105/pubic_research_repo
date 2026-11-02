"""Causal, observation-level book signals. Never use labels or future rows."""
from collections import deque
from .data import NS, SCALE


def ofi_event(previous, current):
    """Cont-style best-quote net flow; aggregated feed proxy, not individual orders."""
    bp, bq = current['bids'][0]; ap, aq = current['asks'][0]
    pbp, pbq = previous['bids'][0]; pap, paq = previous['asks'][0]
    return ((bq if bp >= pbp else 0) - (pbq if bp <= pbp else 0)
            - (aq if ap <= pap else 0) + (paq if ap >= pap else 0)) / SCALE


def enrich(states):
    result = []; prev = None; first = None; history = deque()
    windows = {1: deque(), 5: deque()}; sums = {1: [0., 0.], 5: [0., 0.]}
    for row in states:
        r = dict(row)
        if not r['valid'] or (prev is not None and r['segment'] != prev['segment']):
            prev = None; first = None; history.clear()
            for h in windows: windows[h].clear(); sums[h] = [0., 0.]
        if not r['valid']:
            result.append(r); continue
        t = r['t']; first = t if first is None else first
        qb = r['bids'][0][1] / SCALE; qa = r['asks'][0][1] / SCALE
        r['qi'] = (qb - qa) / (qb + qa)
        r['book_pressure'] = (r['ask'] * qb + r['bid'] * qa) / (qb + qa)
        r['obi6'] = (r['B'] - r['A']) / (r['B'] + r['A']) if r['B'] + r['A'] else 0.
        wb = sum(q / (i+1) for i,q in enumerate(r['bbands']))
        wa = sum(q / (i+1) for i,q in enumerate(r['abands']))
        r['weighted6'] = (wb-wa)/(wb+wa) if wb+wa else 0.
        e = ofi_event(prev, r) if prev is not None else 0.
        for h in windows:
            w = windows[h]; z = sums[h]
            w.append((t, e, qb+qa)); z[0] += e; z[1] += qb+qa
            while w and w[0][0] <= t - h*NS:
                _, old_e, old_d = w.popleft(); z[0] -= old_e; z[1] -= old_d
            r[f'ofi{h}'] = z[0] / (z[1]/len(w)) if t-first >= h*NS and z[1] > 0 else None
        history.append((t,r['mid']))
        while len(history) > 1 and history[1][0] <= t-NS: history.popleft()
        r['momentum1'] = (r['mid']/history[0][1]-1)*10000 if history[0][0] <= t-NS else None
        r['combo'] = min(r['obi6']/0.2,r['ofi1']/0.25) if r['ofi1'] is not None else None
        result.append(r); prev = r
    return result
