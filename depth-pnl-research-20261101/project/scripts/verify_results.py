#!/usr/bin/env python3
"""Independent output reconciliation, boundaries, and source read-only verification."""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--results',default='results')
    p.add_argument('--data',default='/home/ubuntu/reports/binance_depth5_ws/full_var_20261101T031206Z')
    args=p.parse_args(); out=Path(args.results); source=Path(args.data)
    summary=json.loads((out/'summary.json').read_text()); audit=json.loads((out/'audit.json').read_text())
    checks=0
    def check(ok,message):
        nonlocal checks
        assert ok,message
        checks+=1
    def close(a,b,message):
        check(math.isclose(float(a),float(b),abs_tol=1e-8),message)
    for name,record in audit['files'].items():
        check(hashlib.sha256((source/name).read_bytes()).hexdigest()==record['sha256'],f'Changed original {name}')
    for name,record in audit['review_only'].items():
        check(hashlib.sha256(Path(name).read_bytes()).hexdigest()==record['sha256'],f'Changed reference {name}')
    for name,digest in summary['source_sha256'].items():
        check(hashlib.sha256(Path(name).read_bytes()).hexdigest()==digest,f'Source code changed {name}')
    def rows(name):
        return list(csv.DictReader((out/name).open()))
    window=summary['window']; start=window['start_mono_ns']; split=window['split_mono_ns']; end=window['end_mono_ns_exclusive']
    check(end-start==3600*10**9,'Not exactly one hour')
    def verify_trades(name,record,lo,hi,fee,latency):
        trades=rows(name); previous=lo-1
        check(len(trades)==record['trades'],f'{name} count')
        check(record['valid_complete_result'],f'{name} unresolved')
        for t in trades:
            si=int(t['entry_signal_mono_ns']); ei=int(t['entry_mono_ns'])
            so=int(t['exit_signal_mono_ns']); eo=int(t['exit_mono_ns'])
            check(lo<=si<ei<=so<eo<hi,f'{name} out-of-window or noncausal fill')
            check(ei>previous,f'{name} overlapping positions');previous=eo
            check(ei-si>=latency*1e6 and eo-so>=latency*1e6,f'{name} latency')
            q=float(t['quantity_btc']); entry=float(t['entry_price']); exit=float(t['exit_price'])
            close(q,summary['config']['quantity_btc'],'quantity')
            close((exit-entry)*q,t['gross_pnl'],'gross')
            close(entry*q*fee/10000,t['entry_fee'],'entry fee')
            close(exit*q*fee/10000,t['exit_fee'],'exit fee')
            close(float(t['gross_pnl'])-float(t['entry_fee'])-float(t['exit_fee']),t['net_pnl'],'net')
            close((eo-ei)/1e9,t['hold_seconds'],'duration')
        for key in ['gross_pnl','net_pnl']:
            close(sum(float(t[key]) for t in trades),record[key],f'{name} total {key}')
        close(sum(float(t['entry_fee'])+float(t['exit_fee']) for t in trades),record['fees'],f'{name} total fees')
    def verify_curve(name,record,lo,hi):
        curve=rows(name); peak=0.; dd=0.; prev=lo-1
        for r in curve:
            t=int(r['mono_ns']); check(lo<=t<hi and t>=prev,'curve times');prev=t
            v=float(r['equity']);peak=max(peak,v);dd=max(dd,peak-v)
            close(peak-v,r['drawdown'],'curve drawdown')
        close(dd,record['mtm_max_drawdown'],'max drawdown')
        close(curve[-1]['equity'],record['net_pnl'],'terminal equity')
        close(curve[-1]['position_btc'],0,'terminal position')
    for name,lo,hi,record in [('train',start,split,summary['train']),('test',split,end,summary['test']),('buy_hold',split,end,summary['buy_hold_test'])]:
        verify_trades(name+'_trades.csv',record,lo,hi,0,100)
        verify_curve(name+'_equity.csv',record,lo,hi)
    for row in summary['sensitivity']:
        latency=row['latency_ms'];fee=row['fee_bps_per_side'];tag=f'lat{latency}_fee{fee}'
        records=json.loads((out/(tag+'_summary.json')).read_text())
        verify_trades(tag+'_trades.csv',records['strategy'],split,end,fee,latency)
        verify_trades(tag+'_train_trades.csv',records['train_fixed_params'],start,split,fee,latency)
        verify_trades(tag+'_buy_hold_trades.csv',records['buy_hold'],split,end,fee,latency)
        verify_curve(tag+'_equity.csv',records['strategy'],split,end)
        close(records['strategy']['net_pnl'],row['net_pnl'],'scenario summary')
        close(records['strategy']['net_pnl']+records['train_fixed_params']['net_pnl'],row['stitched_hour_net_pnl'],'stitched scenario')
    verify_curve('stitched_hour_equity.csv',summary['descriptive_stitched_hour'],start,end)
    close(summary['train']['net_pnl']+summary['test']['net_pnl'],summary['descriptive_stitched_hour']['net_pnl'],'stitched baseline')
    check(summary['test']['capital_return'] is None,'Invented return on capital')
    result={'passed':True,'assertions':checks,'scenarios':len(summary['sensitivity']),
            'source_hashes_unchanged':True,'boundary_and_accounting_checks':True}
    (out/'validation.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    main()
