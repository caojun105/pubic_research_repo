import argparse
import bisect
import csv
import itertools
import json
import platform
import shlex
import sys
from pathlib import Path
from .data import NS, SCALE, audit, iso, load, replay, sha, units
from .backtest import simulate, summarize
from .report import render

DEFAULT_DATA = '/home/ubuntu/reports/binance_depth5_ws/full_var_20261101T031206Z'


def write_json(p, x):
    p.write_text(json.dumps(x, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def write_csv(p, rows):
    if not rows:
        p.write_text(''); return
    with p.open('w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description='只读原始数据的一小时因果盘口离线研究')
    parser.add_argument('--data', default=DEFAULT_DATA)
    parser.add_argument('--output', default='results')
    parser.add_argument('--config', default='config.json')
    parser.add_argument('--audit-only', action='store_true')
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    source = Path(args.data).resolve(); out = Path(args.output).resolve()
    if out == source or source in out.parents:
        raise ValueError('Output must not be inside the read-only data source')
    out.mkdir(parents=True, exist_ok=True)
    print('Auditing and hashing inputs...', flush=True)
    a = audit(source)
    old = source / 'strategy_depth_1h/summary.json'
    script = source.parent / 'depth_strategy_1h.py'
    a['review_only'] = {str(p): {'sha256':sha(p)} for p in [old,script] if p.exists()}
    if old.exists():
        a['old_summary_untrusted'] = json.loads(old.read_text())
    write_json(out/'audit.json', a)
    if args.audit_only:
        return
    for name, info in a['files'].items():
        if info.get('parse_errors'):
            raise ValueError(f'Malformed JSON in {name}')
    raw_info = a['files']['binance_raw.jsonl']
    if set(raw_info['symbols']) != {'BTCUSDT'}:
        raise ValueError('Expected only BTCUSDT')
    if raw_info['local_mono_ns']['backwards']:
        raise ValueError('Monotonic clock went backwards; unsafe ordering')
    qty = units(config['quantity_btc'])
    if qty <= 0 or not 0 < config['train_seconds'] < config['window_seconds']:
        raise ValueError('Invalid quantity or split')
    if min(config['latencies_ms']) < 0 or min(config['fees_bps']) < 0:
        raise ValueError('Negative latency/fee not supported')
    if config['close_reserve_s'] <= max(config['latencies_ms']) / 1000:
        raise ValueError('Close reserve must exceed maximum latency')
    print('Reconstructing causal book...', flush=True)
    states, counts = replay(load(source/'binance_snapshots.jsonl'), load(source/'binance_raw.jsonl'), qty, config['max_receive_gap_ms'])
    valid = [r for r in states if r['valid']]
    start = valid[0]['t'] + round(config['start_offset_seconds'] * NS)
    end = start + round(config['window_seconds'] * NS)
    split = start + round(config['train_seconds'] * NS)
    if start < valid[0]['t'] or end > valid[-1]['t']:
        raise ValueError('Requested full window not covered by reconstructed data')
    window_states = [r for r in states if start <= r['t'] < end]
    anchor = valid[0]['utc'] - valid[0]['t']
    a['reconstruction'] = {'counts':counts, 'valid_states':len(valid), 'window_states':len(window_states),
                           'first_valid_utc':iso(valid[0]['utc']), 'last_valid_utc':iso(valid[-1]['utc']),
                           'valid_coverage_seconds':(valid[-1]['t']-valid[0]['t'])/NS,
                           'snapshot_price_range_policy':'fixed original snapshot floor/ceiling; outside levels never executable'}
    ts = [r['t'] for r in states]; comparison = {'compared':0, 'best_price_mismatch':0,'uid_mismatch':0,'unavailable':0}
    for r in load(source/'aligned_1s.jsonl'):
        j = bisect.bisect_right(ts, r['aligned_mono_ns']) - 1
        d = r['binance'].get('data')
        if j < 0 or not states[j]['valid'] or not d or not d.get('valid'):
            comparison['unavailable'] += 1; continue
        b = states[j]; comparison['compared'] += 1
        comparison['best_price_mismatch'] += (units(d['bids20'][0][0]) != round(b['bid']*SCALE) or units(d['asks20'][0][0]) != round(b['ask']*SCALE))
        comparison['uid_mismatch'] += b['uid'] != d['lastUpdateId']
    a['aligned_crosscheck_not_execution_source'] = comparison
    write_json(out/'audit.json',a)
    features=[]
    for r in window_states:
        if r['valid']:
            row={k:r[k] for k in ['t','utc','uid','segment','bid','ask','mid','bid1','B','A']}
            row.update({f'{side}_band_{i}':r[key][i] for side,key in [('bid','bbands'),('ask','abands')] for i in range(6)})
            features.append(row)
    write_csv(out/'book_features.csv',features)
    kw = dict(qty=qty, max_hold_s=config['max_hold_s'], entry_sustain_ms=config['entry_sustain_ms'],
              exit_sustain_ms=config['exit_sustain_ms'],close_reserve_s=config['close_reserve_s'])
    grid=[]; best=None; best_score=None
    print('Training parameter grid (test data excluded)...',flush=True)
    for b, ri, ro in itertools.product(config['bid1_min_grid'],config['ratio_in_grid'],config['ratio_out_grid']):
        p = {'bid1_min':b,'ratio_in':ri,'ratio_out':ro}
        run = simulate(window_states,start,split,p,latency_ms=100,fee_bps=0,**kw)
        s=run['summary']; grid.append({**p,**{k:v for k,v in s.items() if not isinstance(v,dict)}})
        if s['valid_complete_result'] and (best is None or s['net_pnl'] > best_score):
            best=p; best_score=s['net_pnl']
    if best is None:
        raise ValueError('No complete training run; do not select parameters')
    write_csv(out/'training_grid.csv',grid)
    train=simulate(window_states,start,split,best,latency_ms=100,fee_bps=0,**kw)
    baseline=simulate(window_states,split,end,best,latency_ms=100,fee_bps=0,**kw)
    bh=simulate(window_states,split,end,best,latency_ms=100,fee_bps=0,benchmark=True,**kw)
    # Full-hour descriptive result is stitched from independent flat-at-boundary segments.
    sensitivity=[]
    print('Evaluating fixed parameters: 4 latencies x 5 fees...',flush=True)
    for latency,fee in itertools.product(config['latencies_ms'],config['fees_bps']):
        run=simulate(window_states,split,end,best,latency_ms=latency,fee_bps=fee,**kw)
        bench=simulate(window_states,split,end,best,latency_ms=latency,fee_bps=fee,benchmark=True,**kw)
        train_scenario=simulate(window_states,start,split,best,latency_ms=latency,fee_bps=fee,**kw)
        tag=f'lat{latency}_fee{fee}'
        write_csv(out/f'{tag}_train_trades.csv',train_scenario['trades'])
        write_csv(out/f'{tag}_trades.csv',run['trades'])
        write_csv(out/f'{tag}_equity.csv',run['equity'])
        write_csv(out/f'{tag}_buy_hold_trades.csv',bench['trades'])
        write_json(out/f'{tag}_summary.json',{'strategy':run['summary'],'buy_hold':bench['summary'],'train_fixed_params':train_scenario['summary']})
        sensitivity.append({'latency_ms':latency,'fee_bps_per_side':fee,
                            **{k:v for k,v in run['summary'].items() if not isinstance(v,dict)},
                            'train_net_pnl':train_scenario['summary']['net_pnl'],
                            'stitched_hour_net_pnl':train_scenario['summary']['net_pnl']+run['summary']['net_pnl'],
                            'buy_hold_net_pnl':bench['summary']['net_pnl'],
                            'buy_hold_valid_complete_result':bench['summary']['valid_complete_result']})
    for name,run in [('train',train),('test',baseline),('buy_hold',bh)]:
        write_csv(out/f'{name}_trades.csv',run['trades']);write_csv(out/f'{name}_equity.csv',run['equity'])
    stitched_equity = [dict(r) for r in train['equity']]
    stitched_equity += [{**r, 'equity':r['equity']+train['summary']['net_pnl'], 'realized_net':r['realized_net']+train['summary']['net_pnl']} for r in baseline['equity']]
    peak=0.0
    for r in stitched_equity:
        peak=max(peak,r['equity']);r['drawdown']=peak-r['equity']
    stitched_trades=train['trades']+baseline['trades']
    stitched_summary=summarize(stitched_trades,stitched_equity,{},train['summary']['unresolved'] or baseline['summary']['unresolved'])
    write_csv(out/'stitched_hour_trades.csv',stitched_trades)
    write_csv(out/'stitched_hour_equity.csv',stitched_equity)
    write_csv(out/'sensitivity.csv',sensitivity)
    no_trade={'trades':0,'gross_pnl':0,'fees':0,'net_pnl':0,'mtm_max_drawdown':0,'capital_return':None}
    summary={'instrument':'Binance spot BTCUSDT','quote_unit':'USDT','config':config,
             'window':{'start_mono_ns':start,'split_mono_ns':split,'end_mono_ns_exclusive':end,
                       'start_utc_anchored':iso(start+anchor),'split_utc_anchored':iso(split+anchor),'end_utc_exclusive_anchored':iso(end+anchor),
                       'duration_seconds':(end-start)/NS,'train_seconds':(split-start)/NS,'test_seconds':(end-split)/NS},
             'selected_params':best,'selection_objective':'training net PnL, 0 bps, 100 ms; ties use first grid entry',
             'train':train['summary'],'test':baseline['summary'],'buy_hold_test':bh['summary'],'no_trade':no_trade,
             'descriptive_stitched_hour':stitched_summary,
             'descriptive_stitched_hour_net_pnl':train['summary']['net_pnl']+baseline['summary']['net_pnl'],
             'sensitivity':sensitivity,'python':platform.python_version(),
             'reproduce_command':f'python3 -m depth_research --data {shlex.quote(str(source))} --config {shlex.quote(str(Path(args.config).resolve()))} --output {shlex.quote(str(out))}',
             'source_sha256':{str(p):sha(p) for p in sorted(Path('depth_research').glob('*.py'))},
             'config_sha256':sha(args.config)}
    write_json(out/'summary.json',summary)
    (out/'report.html').write_text(render(summary,baseline,bh,sensitivity,a))
    print(json.dumps({'selected_params':best,'test':baseline['summary'],'buy_hold':bh['summary'], 'output':str(out)},ensure_ascii=False,indent=2))
    if not all(r['valid_complete_result'] and r['buy_hold_valid_complete_result'] for r in sensitivity):
        sys.exit('Incomplete position accounting: see scenario JSON; results must not be treated as final profitability')


if __name__ == '__main__':
    main()
