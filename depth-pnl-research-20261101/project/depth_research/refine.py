"""R03: bounded, retrospective cost-aware refinement; no live orders.

Run from project/: python -m depth_research.refine --release-dir PATH
Raw data are read-only. All splits use whole, previously audited R02 hours.
"""
import argparse
import collections
import json
import math
from pathlib import Path
import numpy as np
from .data import NS, load, replay, sha, units, iso
from .features import enrich
from .backtest import walk
from .__main__ import write_csv, write_json


FEATURES = ['qi', 'obi6', 'weighted6', 'ofi1', 'ofi5', 'momentum1',
            'momentum10', 'momentum30', 'ema1', 'ema5', 'spread_bps']


def candidates():
    base = dict(signal='obi6', entry=.4, exit=0., sustain_ms=500,
                exit_ms=300, min_hold=0, max_hold=30, cooldown=0,
                stop_bps=0., take_bps=0., gate='none')
    result = []
    def add(name, group, **kw):
        result.append(dict(base, **{'id': name, 'group': group}) | kw)
    add('obi6_r02', 'reference')
    add('qi_r02', 'reference', signal='qi', entry=.2)
    for threshold in [.4, .7]:
        for hold in [30, 90]:
            add(f'hysteresis_{threshold}_{hold}', 'hysteresis', entry=threshold,
                exit=-.2, min_hold=5, max_hold=hold, cooldown=2)
    for half in [1, 5]:
        for threshold in [.4, .6]:
            add(f'smooth_{half}_{threshold}', 'smoothed_depth', signal=f'ema{half}',
                entry=threshold, exit=-.2, min_hold=5, max_hold=90, cooldown=5)
    for gate in ['qi', 'ofi', 'qi_ofi']:
        for hold in [30, 90]:
            add(f'consensus_{gate}_{hold}', 'consensus', gate=gate,
                exit=-.2, min_hold=5, max_hold=hold, cooldown=2)
    for trend in [10, 30]:
        for hold in [30, 90]:
            add(f'trend_{trend}_{hold}', 'trend_filter', gate=f'trend{trend}',
                exit=-.2, min_hold=5, max_hold=hold, cooldown=5)
    for hold in [30, 60, 120]:
        for threshold in [.4, .7]:
            add(f'hold_{hold}_{threshold}', 'fixed_horizon', entry=threshold,
                exit=-2., max_hold=hold, cooldown=5, stop_bps=5, take_bps=8)
    for horizon in [10, 30, 60]:
        for threshold in [2.5, 4.]:
            add(f'ridge_{horizon}_{threshold}', 'predicted_edge',
                signal=f'ridge{horizon}', entry=threshold, exit=-1e9,
                max_hold=horizon, cooldown=5, sustain_ms=500)
    return result


def make_plan():
    prior = json.loads(Path('experiments/r02/results/summary.json').read_text())
    chunks = prior['common_clean_chunks']
    assert len(chunks) == 12
    return dict(id='R03', status='retrospective_exploration_on_previously_seen_data',
                quantity_btc=.01, initial_cash_usdt=1000., capital_compounding=False,
                fees_bps=[0., .2, 1.], latencies_ms=[100, 500],
                primary_fee_bps=1., stress_extra_cost_bps_per_side=.2,
                train=chunks[:4], validation=chunks[4:6], test=chunks[6:],
                excluded=prior['excluded_chunks'], candidates=candidates(),
                ridge=dict(horizons=[10, 30, 60], alpha=100., sample_seconds=1,
                           features=FEATURES, fit_hours=4, label='delayed_executable_roundtrip_gross_bps'),
                selection='For each declared fee independently: >=20 validation trades at both latencies; both validation hours profitable at both latencies; maximize min_latency(net_pnl - 0.5 * liquidation_MDD); otherwise cash. Tie: candidate id. Freeze before test.',
                test_qualification='Chosen on validation only: >=30 trades, >=5/6 profitable test hours, positive PnL excluding top3 trades, positive test PnL at both latencies, liquidation_MDD <= 0.5*net_pnl. Passing is provisional, not live validation.',
                execution='Long/flat taker, next event after latency, visible VWAP, 2s close reserve, no position across hourly gaps; 1000ms raw receive gap hard stop inherited from R02.',
                limitation='All 12 hours were previously viewed in R02. Chronological split reduces mechanical leakage but is not a new untouched holdout; same overnight regime, multiple candidates, no annualization.',
                r02_summary_sha256=sha('experiments/r02/results/summary.json'))


def causal_extra(t, mid, obi):
    out = {}
    for h in [10, 30]:
        idx = np.searchsorted(t, t-h*NS, side='right')-1
        out[f'momentum{h}'] = np.where(idx >= 0, (mid/mid[np.maximum(idx, 0)]-1)*10000, np.nan)
    for h in [1, 5]:
        z = np.empty(len(t)); z[0] = obi[0]
        for i in range(1, len(t)):
            decay = math.exp(-math.log(2)*(int(t[i])-int(t[i-1]))/NS/h)
            z[i] = decay*z[i-1]+(1-decay)*obi[i]
        z[t-t[0] < 5*h*NS] = np.nan
        out[f'ema{h}'] = z
    return out


def build_cache(root, chunk, cache, audit):
    folder = root/'extracted'/chunk
    for name, expected in audit['input_hashes'].items():
        assert sha(folder/name) == expected, name
    code_hash = {n: sha(Path('depth_research')/n) for n in ['data.py', 'features.py', 'backtest.py', 'refine.py']}
    identity = dict(input_hashes=audit['input_hashes'], code_hashes=code_hash, quantity=.01)
    file = cache/f'{chunk}.npz'; meta = cache/f'{chunk}.json'
    if file.exists() and meta.exists() and json.loads(meta.read_text()) == identity:
        with np.load(file) as data:
            return {k: data[k] for k in data.files}
    raw = []
    with (folder/'binance_raw.jsonl').open() as f:
        for line in f:
            r = json.loads(line)
            if r['data']['e'] == 'depthUpdate': raw.append(r)
    states, counts = replay(load(folder/'binance_snapshots.jsonl'), raw, units('.01'))
    assert counts == audit['reconstruction']
    states = enrich(states)
    with (folder/'aligned_1s.jsonl').open() as f: aligned = json.loads(next(f))
    start = aligned['aligned_mono_ns']; end = start+3600*NS
    assert all(r['valid'] for r in states)
    t = np.array([r['t'] for r in states], dtype=np.int64)
    data = dict(t=t, utc=np.array([r['utc'] for r in states], dtype=np.int64),
                start=np.array(start, dtype=np.int64), end=np.array(end, dtype=np.int64))
    for name in ['mid', 'qi', 'obi6', 'weighted6', 'ofi1', 'ofi5', 'momentum1']:
        data[name] = np.array([r[name] if r[name] is not None else np.nan for r in states], dtype=float)
    for name, side in [('buy', 'asks'), ('sell', 'bids')]:
        data[name] = np.array([walk(r[side], units('.01'))[0] for r in states])
    data['spread_bps'] = np.array([(r['ask']-r['bid'])/r['mid']*10000 for r in states])
    data.update(causal_extra(t, data['mid'], data['obi6']))
    cache.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(file, **data); write_json(meta, identity)
    return data


def fit_models(dataset, plan):
    from sklearn.linear_model import Ridge
    from sklearn.preprocessing import StandardScaler
    models = {}
    for h in plan['ridge']['horizons']:
        xx, yy = [], []
        for name in plan['train']:
            d = dataset[name]; t = d['t']
            sample = np.arange(int(d['start']), int(d['end']), NS)
            ix = np.searchsorted(t, sample, side='right')-1
            ix = ix[ix >= 0]
            # Entry and exit are strictly later events, after 100ms execution latency.
            entry = np.searchsorted(t, t[ix]+100_000_000)
            safe_entry = np.minimum(entry, len(t)-1)
            exit_signal = np.searchsorted(t, t[safe_entry]+h*NS)
            exit_fill = np.searchsorted(t, t[np.minimum(exit_signal, len(t)-1)]+100_000_000)
            ok = (entry < len(t)) & (exit_signal < len(t)) & (exit_fill < len(t))
            ix, entry, exit_fill = ix[ok], entry[ok], exit_fill[ok]
            x = np.column_stack([d[k][ix] for k in FEATURES])
            y = (d['sell'][exit_fill]/d['buy'][entry]-1)*10000
            ok = np.all(np.isfinite(x), axis=1) & (t[exit_fill] < int(d['end'])-2*NS)
            xx.append(x[ok]); yy.append(y[ok])
        x = np.concatenate(xx); y = np.concatenate(yy)
        # Clip fixed ranges, not full-sample quantiles. No test-dependent normalization.
        x = np.clip(x, -100., 100.)
        scaler = StandardScaler().fit(x)
        model = Ridge(alpha=plan['ridge']['alpha']).fit(scaler.transform(x), y)
        models[f'ridge{h}'] = dict(horizon=h, n=len(y), mean=scaler.mean_.tolist(),
            scale=scaler.scale_.tolist(), coefficients=model.coef_.tolist(),
            intercept=float(model.intercept_), training_target_mean_bps=float(y.mean()))
    return models


def predictions(d, models):
    x = np.clip(np.column_stack([d[k] for k in FEATURES]), -100., 100.)
    return {key: ((x-np.array(m['mean']))/np.array(m['scale']))@np.array(m['coefficients'])+m['intercept']
            for key, m in models.items()}


def signal_flags(d, cfg):
    signal = d[cfg['signal']]
    enter = np.isfinite(signal) & (signal >= cfg['entry'])
    weak = ~np.isfinite(signal) | (signal <= cfg['exit'])
    gate = cfg['gate']
    if gate in ['qi', 'qi_ofi']: enter &= d['qi'] >= .5
    if gate in ['ofi', 'qi_ofi']: enter &= d['ofi5'] >= .5
    if gate.startswith('trend'): enter &= d['momentum'+gate[5:]] > 0.
    return enter, weak


def execute(d, cfg, latency_ms, benchmark=False):
    """Compact event replay, fee-independent rules. Fees are reconciled afterwards.

    Returns event equity at zero fees and fee exposure (realized turnover plus
    hypothetical exit turnover for liquidation marks). Stop/target use observed
    executable bid; actual exits occur only after the configured latency.
    """
    tarr = d['t']; buy = d['buy']; sell = d['sell']; mid = d['mid']
    enter, weak = signal_flags(d, cfg)
    start, end = int(d['start']), int(d['end'])
    latency = round(latency_ms*1e6); close = end-2*NS
    cutoff = close-round(cfg['max_hold']*NS)-latency
    sustain = round(cfg['sustain_ms']*1e6); exit_sustain = round(cfg['exit_ms']*1e6)
    minhold = round(cfg['min_hold']*NS); maxhold = round(cfg['max_hold']*NS)
    cooldown = round(cfg['cooldown']*NS)
    n = len(tarr); equity = np.zeros(n); mid_equity = np.zeros(n); exposure = np.zeros(n)
    mid_exposure = np.zeros(n); inventory = np.zeros(n, dtype=bool)
    position = -1; pending = 0; signal = -1; entry_signal = -1
    in_since = out_since = -1; next_entry = start; realized = turnover = 0.
    trades = []; reason = ''; attempted = False; qty = .01
    for i in range(n):
        t = int(tarr[i]); filled = False
        if not start <= t < end: continue
        if pending and t > int(tarr[signal]) and t >= int(tarr[signal])+latency:
            if pending == 1:
                if t < cutoff:
                    position = i; entry_signal = signal; filled = True
                    in_since = out_since = -1
                pending = 0
            else:
                gross = (sell[i]-buy[position])*qty
                notional = (sell[i]+buy[position])*qty
                trades.append(dict(entry_signal_index=entry_signal, entry_index=position,
                                   exit_signal_index=signal, exit_index=i,
                                   entry_price=float(buy[position]), exit_price=float(sell[i]),
                                   gross_pnl=float(gross), turnover=float(notional),
                                   hold_seconds=(t-int(tarr[position]))/NS, exit_reason=reason))
                realized += gross; turnover += notional; position = -1; pending = 0
                in_since = out_since = -1; next_entry = t+cooldown; filled = True
        if position >= 0:
            equity[i] = realized+(sell[i]-buy[position])*qty
            mid_equity[i] = realized+(mid[i]-buy[position])*qty
            exposure[i] = turnover+(sell[i]+buy[position])*qty
            mid_exposure[i] = turnover+buy[position]*qty
            inventory[i] = True
        else:
            equity[i] = mid_equity[i] = realized
            exposure[i] = mid_exposure[i] = turnover
        if pending or filled: continue
        if position >= 0:
            held = t-int(tarr[position]); move = (sell[i]/buy[position]-1)*10000
            reason = ''
            if t >= close: reason = 'boundary'
            elif not benchmark and cfg['stop_bps'] and move <= -cfg['stop_bps']: reason = 'stop'
            elif not benchmark and cfg['take_bps'] and move >= cfg['take_bps']: reason = 'target'
            elif not benchmark and held >= maxhold: reason = 'max_hold'
            elif not benchmark and held >= minhold:
                if weak[i]:
                    if out_since < 0: out_since = t
                    if t-out_since >= exit_sustain: reason = 'weak'
                else: out_since = -1
            if reason: pending = -1; signal = i
        elif t < cutoff and t >= next_entry:
            ok = not attempted if benchmark else enter[i]
            if ok:
                if in_since < 0: in_since = t
                if benchmark or t-in_since >= sustain:
                    pending = 1; signal = i; in_since = -1; attempted = True
            else: in_since = -1
    assert position < 0, 'Unresolved position: whole-hour result is invalid'
    in_window = (tarr >= start) & (tarr < end)
    assert in_window.any()
    idx = np.flatnonzero(in_window)
    assert math.isclose(equity[idx[-1]], sum(r['gross_pnl'] for r in trades), abs_tol=1e-8)
    return dict(trades=trades, indices=idx, equity=equity[idx], mid_equity=mid_equity[idx],
                exposure=exposure[idx], mid_exposure=mid_exposure[idx], inventory=inventory[idx])


def measure(run, fee, extra=0., capital=1000.):
    rate = (fee+extra)/10000
    curve = run['equity']-run['exposure']*rate
    mid_curve = run['mid_equity']-run['mid_exposure']*rate
    peak = np.maximum.accumulate(np.maximum(curve, 0.))
    mid_peak = np.maximum.accumulate(np.maximum(mid_curve, 0.))
    pnls = np.array([r['gross_pnl']-r['turnover']*rate for r in run['trades']])
    gross = sum(r['gross_pnl'] for r in run['trades'])
    turn = sum(r['turnover'] for r in run['trades'])
    cash = capital
    for r in run['trades']:
        assert cash >= r['entry_price']*.01*(1+rate), 'Insufficient spot cash'
        cash += r['gross_pnl']-r['turnover']*rate
    return dict(trades=len(pnls), gross_pnl=gross, net_pnl=float(pnls.sum()), turnover=turn,
        fees=turn*fee/10000, extra_cost=turn*extra/10000,
        liquidation_mdd=float(np.max(peak-curve)), mid_mdd=float(np.max(mid_peak-mid_curve)),
        liquidation_mdd_pct=float(np.max((peak-curve)/(capital+peak))*100),
        return_pct=float(pnls.sum()/capital*100),
        win_rate=float(np.mean(pnls>1e-10)) if len(pnls) else None,
        break_even_fee_bps=gross/turn*10000 if turn else None,
        mean_hold_seconds=float(np.mean([r['hold_seconds'] for r in run['trades']])) if len(pnls) else None,
        top3_pnl=float(np.sort(pnls)[-3:].sum()), without_top3=float(np.sort(pnls)[:-3].sum()),
        exposure_event_fraction=float(run['inventory'].mean()),
        curve_min=float(curve.min()), curve_max=float(curve.max()))


def aggregate(rows, capital=1000.):
    equity = peak = dd = dd_pct = 0.; net = gross = turn = 0.; pnls = []
    for r in rows:
        # Exact stitched MDD can be composed from each hour's min, max and MDD.
        dd = max(dd, r['liquidation_mdd'], peak-(equity+r['curve_min']))
        dd_pct = max(dd_pct, r['liquidation_mdd_pct'],
                     max(0., peak-(equity+r['curve_min']))/(capital+peak)*100)
        peak = max(peak, equity+r['curve_max']); equity += r['net_pnl']
        net += r['net_pnl']; gross += r['gross_pnl']; turn += r['turnover']
        pnls.extend(r.get('_trade_pnls', []))
    trades = sum(r['trades'] for r in rows)
    return dict(hours=len(rows), trades=trades, net_pnl=net, gross_pnl=gross, turnover=turn,
        return_pct=net/capital*100, liquidation_mdd=dd,
        # Percentage recomputed precisely from full curves for selected strategies below.
        positive_hours=sum(r['net_pnl']>0 for r in rows),
        worst_hour=min((r['net_pnl'] for r in rows), default=0.),
        break_even_fee_bps=gross/turn*10000 if turn else None,
        net_per_trade=net/trades if trades else None,
        without_top3=sum(sorted(pnls)[:-3]), top3_pnl=sum(sorted(pnls)[-3:]),
        win_rate=sum(v>1e-10 for v in pnls)/len(pnls) if pnls else None)


def choose(rows, plan, fee):
    ranked = []
    for cfg in plan['candidates']:
        bylat = [aggregate([r for r in rows if r['id']==cfg['id'] and r['fee_bps']==fee and
                            r['latency_ms']==lat and r['split']=='validation']) for lat in plan['latencies_ms']]
        eligible = all(a['trades']>=20 and a['positive_hours']==2 for a in bylat)
        score = min(a['net_pnl']-.5*a['liquidation_mdd'] for a in bylat)
        ranked.append(dict(id=cfg['id'], eligible=eligible, score=score, by_latency=bylat))
    viable = sorted([r for r in ranked if r['eligible'] and r['score']>0], key=lambda r: (-r['score'], r['id']))
    return dict(fee_bps=fee, chosen=viable[0]['id'] if viable else 'cash', ranking=ranked)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--release-dir', required=True)
    parser.add_argument('--output', default='experiments/r03')
    parser.add_argument('--cache', default='/workspace/scratch/da9bc8c1119b/r03-cache')
    parser.add_argument('--plan-only', action='store_true')
    args = parser.parse_args(); out = Path(args.output); out.mkdir(parents=True, exist_ok=True)
    plan_path = out/'plan.json'
    if not plan_path.exists(): write_json(plan_path, make_plan())
    plan = json.loads(plan_path.read_text()); plan_hash = sha(plan_path)
    assert plan == make_plan(), 'Existing frozen plan differs; create a new experiment instead'
    if args.plan_only:
        print(json.dumps(dict(candidates=len(plan['candidates']), plan_sha256=plan_hash))); return
    result_dir = out/'results'; result_dir.mkdir(exist_ok=True)
    audit = {a['chunk']:a for a in json.loads(Path('experiments/r02/results/audit.json').read_text())}
    dataset = {}
    for name in plan['train']+plan['validation']+plan['test']:
        dataset[name] = build_cache(Path(args.release_dir), name, Path(args.cache), audit[name])
        print('verified and reconstructed '+name, flush=True)
    models = fit_models(dataset, plan); write_json(result_dir/'models.json', models)
    for d in dataset.values(): d.update(predictions(d, models))
    rows = []; trades = []; verification = dict(r02_equivalence_checks=0, trades_checked=0)
    import csv
    r02 = list(csv.DictReader(Path('experiments/r02/results/hourly.csv').open()))
    selection = None; curves = []; configs = plan['candidates']+[dict(plan['candidates'][0], id='buy_hold', group='reference')]
    for split in ['train', 'validation', 'test']:
        if split == 'test':
            selection = [choose(rows, plan, fee) for fee in plan['fees_bps']]
            write_json(result_dir/'selection.json', dict(plan_sha256=plan_hash, models_sha256=sha(result_dir/'models.json'), selections=selection,
                selection_uses_test=False, previously_seen_data=True))
            print('SELECTION FROZEN: '+json.dumps([{k:s[k] for k in ['fee_bps','chosen']} for s in selection]), flush=True)
        for name in plan[split]:
            d = dataset[name]
            for cfg in configs:
                for lat in plan['latencies_ms']:
                    run = execute(d, cfg, lat, benchmark=cfg['id']=='buy_hold')
                    prev = int(d['start'])-1
                    for tr in run['trades']:
                        si, ei, so, eo = [tr[k] for k in ['entry_signal_index','entry_index','exit_signal_index','exit_index']]
                        assert int(d['start']) <= d['t'][si] < d['t'][ei] <= d['t'][so] < d['t'][eo] < int(d['end'])
                        assert d['t'][ei] > prev and d['t'][ei]-d['t'][si] >= lat*1e6 and d['t'][eo]-d['t'][so] >= lat*1e6
                        assert abs(tr['gross_pnl']-(d['sell'][eo]-d['buy'][ei])*.01) < 1e-9
                        prev = d['t'][eo]; verification['trades_checked'] += 1
                        trades.append(dict(chunk=name, split=split, id=cfg['id'], latency_ms=lat,
                            entry_signal_utc=iso(int(d['utc'][si])), entry_utc=iso(int(d['utc'][ei])),
                            exit_signal_utc=iso(int(d['utc'][so])), exit_utc=iso(int(d['utc'][eo])), **tr))
                    for fee in plan['fees_bps']:
                        stats = measure(run, fee)
                        row = dict(chunk=name, split=split, id=cfg['id'], group=cfg['group'], latency_ms=lat, fee_bps=fee, **stats)
                        row['_trade_pnls'] = [t['gross_pnl']-t['turnover']*fee/10000 for t in run['trades']]
                        rows.append(row)
                        if cfg['id'] in ['obi6_r02','qi_r02'] and fee in [0.,1.]:
                            family = cfg['id'].split('_')[0]
                            ref = next(r for r in r02 if r['chunk']==name and r['family']==family and float(r['latency_ms'])==lat and float(r['fee_bps'])==fee)
                            assert stats['trades']==int(ref['trades'])
                            for key in ['net_pnl', 'gross_pnl']: assert abs(stats[key]-float(ref[key])) < 1e-7
                            assert abs(stats['mid_mdd']-float(ref['mtm_max_drawdown'])) < 1e-7
                            verification['r02_equivalence_checks'] += 1
                        if split=='test' and (cfg['id'] in ['obi6_r02','buy_hold'] or any(s['chosen']==cfg['id'] for s in selection)):
                            # Store one-second sampled charts; statistics use every event.
                            idx = run['indices']; sec = d['t'][idx]//NS
                            pick = np.r_[np.diff(sec)!=0, True]
                            for j in np.flatnonzero(pick):
                                curves.append(dict(chunk=name, id=cfg['id'], latency_ms=lat, fee_bps=fee,
                                    utc=iso(int(d['utc'][idx[j]])), equity=float(run['equity'][j]-run['exposure'][j]*fee/10000)))
            print(f'{split} completed {name}', flush=True)
            write_csv(result_dir/'hourly.csv', [{k:v for k,v in r.items() if not k.startswith('_')} for r in rows])
    aggregates = []
    for split in ['train','validation','test','all_seen']:
        for cfg in configs:
            for lat in plan['latencies_ms']:
                for fee in plan['fees_bps']:
                    rr = [r for r in rows if r['id']==cfg['id'] and r['latency_ms']==lat and r['fee_bps']==fee and (split=='all_seen' or r['split']==split)]
                    aggregates.append(dict(split=split, id=cfg['id'], group=cfg['group'], latency_ms=lat, fee_bps=fee, **aggregate(rr)))
    write_csv(result_dir/'aggregate.csv', aggregates)
    write_csv(result_dir/'trades.csv', trades)
    write_csv(result_dir/'selected_equity_1s.csv', curves)
    chosen = []
    for s in selection:
        fee = s['fee_bps']; name = s['chosen']
        ss = [r for r in aggregates if r['id']==name and r['split']=='test' and r['fee_bps']==fee]
        qualified = bool(ss) and all(r['trades']>=30 and r['positive_hours']>=5 and r['without_top3']>0 and r['net_pnl']>0 and r['liquidation_mdd'] <= .5*r['net_pnl'] for r in ss)
        stress = []
        if name != 'cash':
            cfg = next(c for c in configs if c['id']==name)
            for lat in plan['latencies_ms']:
                rr = []
                for chunk in plan['test']:
                    run = execute(dataset[chunk], cfg, lat)
                    r = measure(run, fee, plan['stress_extra_cost_bps_per_side'])
                    r['_trade_pnls'] = [t['gross_pnl']-t['turnover']*(fee+plan['stress_extra_cost_bps_per_side'])/10000 for t in run['trades']]
                    rr.append(r)
                stress.append(dict(latency_ms=lat, **aggregate(rr)))
        chosen.append(dict(fee_bps=fee, id=name, provisional_test_pass=qualified, test=ss, extra_cost_stress=stress))
    assert sha(plan_path)==plan_hash
    summary = dict(experiment='R03', plan_sha256=plan_hash, candidates=len(plan['candidates']),
        run_count=len(configs)*12*len(plan['latencies_ms']), fee_scenarios=len(rows),
        verification=verification, selected=chosen, unseen_holdout=False,
        source_sha256={str(p):sha(p) for p in Path('depth_research').glob('*.py')},
        input_audit_sha256=sha('experiments/r02/results/audit.json'))
    write_json(result_dir/'summary.json', summary)
    print(json.dumps(summary['selected'], indent=2), flush=True)


if __name__ == '__main__': main()
