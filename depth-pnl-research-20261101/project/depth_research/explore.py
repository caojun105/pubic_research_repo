"""R01: limited literature-derived signals under a shared execution model."""
import argparse
import bisect
import json
import math
import statistics
import sys
from pathlib import Path
from .data import NS, units, load, replay, sha, audit
from .backtest import simulate
from .features import enrich
from .__main__ import write_csv, write_json


def corr(x,y):
    if len(x)<3:return None
    mx=statistics.mean(x);my=statistics.mean(y)
    xx=sum((a-mx)**2 for a in x);yy=sum((a-my)**2 for a in y)
    return sum((a-mx)*(b-my) for a,b in zip(x,y))/math.sqrt(xx*yy) if xx*yy>0 else None


def predictive_samples(states,lo,hi,key,h):
    ts=[r['t'] for r in states]; samples=[]
    # Nonoverlapping clock horizons; as-of book at both endpoints; labels stay within split.
    for t in range(lo,hi-h*NS,h*NS):
        i=bisect.bisect_right(ts,t)-1;j=bisect.bisect_right(ts,t+h*NS)-1
        if i<0 or j<=i:continue
        a,b=states[i],states[j]
        if (not a['valid'] or not b['valid'] or a['segment']!=b['segment']
            or a[key] is None or t-a['t']>NS or t+h*NS-b['t']>NS):continue
        samples.append((a[key],(b['mid']/a['mid']-1)*10000))
    return samples


def check_run(run,lo,hi,fee,latency,qty):
    s=run['summary']
    if not s['valid_complete_result']:raise AssertionError('Incomplete run')
    prev=lo-1
    for r in run['trades']:
        si,ei,so,eo=[r[k] for k in ['entry_signal_mono_ns','entry_mono_ns','exit_signal_mono_ns','exit_mono_ns']]
        assert lo<=si<ei<=so<eo<hi and ei>prev
        assert ei-si>=latency*1e6 and eo-so>=latency*1e6
        assert math.isclose(r['quantity_btc'],float(qty),abs_tol=1e-10)
        gross=(r['exit_price']-r['entry_price'])*float(qty)
        fees=(r['entry_price']+r['exit_price'])*float(qty)*fee/10000
        assert math.isclose(r['net_pnl'],gross-fees,abs_tol=1e-8)
        prev=eo
    assert math.isclose(sum(r['net_pnl'] for r in run['trades']),s['net_pnl'],abs_tol=1e-8)
    peak=0.;dd=0.
    for r in run['equity']:
        peak=max(peak,r['equity']);dd=max(dd,peak-r['equity'])
        assert math.isclose(r['drawdown'],peak-r['equity'],abs_tol=1e-8)
    assert math.isclose(s['mtm_max_drawdown'],dd,abs_tol=1e-8)
    if run['equity']:
        assert run['equity'][-1]['position_btc']==0
        assert math.isclose(run['equity'][-1]['equity'],s['net_pnl'],abs_tol=1e-8)


def main():
    p=argparse.ArgumentParser();p.add_argument('--data',required=True)
    p.add_argument('--plan',default='experiments/r01/plan.json')
    p.add_argument('--output',default='experiments/r01/results')
    args=p.parse_args();source=Path(args.data).resolve();out=Path(args.output).resolve()
    if out==source or source in out.parents:raise ValueError('Read-only source')
    out.mkdir(parents=True,exist_ok=True);plan=json.loads(Path(args.plan).read_text())
    a=audit(source)
    assert all(not v.get('parse_errors') for v in a['files'].values())
    assert set(a['files']['binance_raw.jsonl']['symbols'])=={'BTCUSDT'}
    assert not a['files']['binance_raw.jsonl']['local_mono_ns']['backwards']
    # Retain enough visible levels for all planned quantities, including capacity stress.
    print('Rebuilding raw book and causal features...',flush=True)
    states,counts=replay(load(source/'binance_snapshots.jsonl'),load(source/'binance_raw.jsonl'),units(max(map(float,plan['quantities_btc']))))
    states=enrich(states);valid=[r for r in states if r['valid']]
    start=valid[0]['t'];split=start+plan['train_seconds']*NS;end=start+plan['window_seconds']*NS
    assert valid[-1]['t']>=end
    states=[r for r in states if start<=r['t']<end]
    a['reconstruction']=counts;write_json(out/'audit.json',a)
    kw={k:plan[k] for k in ['max_hold_s','entry_sustain_ms','exit_sustain_ms','close_reserve_s']}
    checked=0
    def run(key,params,lo,hi,fee=0,latency=100,qty=plan['quantity_btc'],benchmark=False):
        nonlocal checked
        r=simulate(states,lo,hi,params,units(qty),latency_ms=latency,fee_bps=fee,signal_key=key,benchmark=benchmark,**kw)
        check_run(r,lo,hi,fee,latency,qty);checked+=1;return r
    selected={};grid=[];train_summaries={}
    # Complete all model selection before evaluating any test trade.
    for key,thresholds in plan['families'].items():
        best=None;best_score=0.;best_train=None
        for threshold in thresholds:
            params={'entry':threshold,'exit':plan['exit_threshold']}
            r=run(key,params,start,split);s=r['summary']
            grid.append({'family':key,'entry':threshold,**s})
            if s['trades']>=5 and s['net_pnl']>best_score:
                best=params;best_score=s['net_pnl'];best_train=s
        selected[key]=best
        train_summaries[key]=best_train
    active=[k for k in selected if selected[k] is not None]
    champion=max(active,key=lambda k:train_summaries[k]['net_pnl']) if active else None
    selected['original']=plan['original_reference']
    train_summaries['original']=run(None,selected['original'],start,split)['summary']
    write_csv(out/'training_grid.csv',grid)
    write_json(out/'selection.json',{'parameters':selected,'training_champion':champion,'selection_uses_test':False})
    print('Selection frozen; evaluating test, fees, delays and sizes...',flush=True)
    comparison=[];sensitivity=[];sizes=[];blocks=[];curves=[]
    for key,params in selected.items():
        if params is None:
            comparison.append({'family':key,'status':'no_positive_eligible_training_candidate','selected':None,'train':None,'test':None})
            continue
        signal=None if key=='original' else key
        base=run(signal,params,split,end)
        trades=base['trades'];ordered=sorted((t['net_pnl'] for t in trades),reverse=True)
        hourly=[sum(t['net_pnl'] for t in trades if split+i*300*NS<=t['exit_mono_ns']<split+(i+1)*300*NS) for i in range(4)]
        s=base['summary']
        comparison.append({'family':key,'status':'evaluated','selected':params,'train':train_summaries[key],
                           'test':s,'top3_pnl':sum(ordered[:3]),'pnl_without_top3':sum(ordered[3:]),
                           'realized_5min_blocks':hourly})
        write_csv(out/f'{key}_test_trades.csv',trades)
        for row in base['equity']:curves.append({'family':key,**row})
        for i,v in enumerate(hourly):blocks.append({'family':key,'block':i+1,'realized_pnl':v})
        for fee in plan['fees_bps']:
            for latency in plan['latencies_ms']:
                r=run(signal,params,split,end,fee=fee,latency=latency)
                sensitivity.append({'family':key,'fee_bps':fee,'latency_ms':latency,**r['summary']})
                write_csv(out/f'{key}_fee{fee}_lat{latency}_trades.csv',r['trades'])
        for qty in plan['quantities_btc']:
            r=run(signal,params,split,end,qty=qty)
            sizes.append({'family':key,'quantity_btc':qty,**r['summary']})
    bh=run(None,selected['original'],split,end,benchmark=True)
    write_csv(out/'buy_hold_trades.csv',bh['trades'])
    # Predictive evidence is separate from execution PnL, with thresholds frozen on train.
    prediction=[]
    for key in plan['families']:
        for h in plan['prediction_horizons_s']:
            tr=predictive_samples(states,start,split,key,h);te=predictive_samples(states,split,end,key,h)
            if not tr:continue
            sx=sorted(x for x,y in tr);low=sx[int(.25*(len(sx)-1))];high=sx[int(.75*(len(sx)-1))]
            for label,pairs in [('train',tr),('test',te)]:
                up=[y for x,y in pairs if x>=high];down=[y for x,y in pairs if x<=low]
                prediction.append({'family':key,'split':label,'horizon_s':h,'n':len(pairs),
                    'pearson_ic':corr([x for x,y in pairs],[y for x,y in pairs]),
                    'train_q25':low,'train_q75':high,'high_n':len(up),'low_n':len(down),
                    'high_mean_bps':statistics.mean(up) if up else None,'low_mean_bps':statistics.mean(down) if down else None,
                    'high_minus_low_bps':statistics.mean(up)-statistics.mean(down) if up and down else None})
    write_csv(out/'prediction.csv',prediction);write_csv(out/'sensitivity.csv',sensitivity)
    write_csv(out/'capacity.csv',sizes);write_csv(out/'five_minute_blocks.csv',blocks)
    write_csv(out/'baseline_equity.csv',curves)
    summary={'experiment':'R01','exploratory_seen_data':True,'plan':plan,'plan_sha256':sha(args.plan),
             'window':{'start':start,'split':split,'end':end},'training_champion':champion,
             'comparison':comparison,'buy_hold':bh['summary'],'no_trade_net_pnl':0,
             'source_sha256':{str(f):sha(f) for f in sorted(Path('depth_research').glob('*.py'))},
             'reproduce_argv':sys.argv,'checks':{'runs_checked':checked,'passed':True},
             'limitations':['Already seen one hour, no untouched holdout','OFI is aggregated-update proxy',
                 'Replay has no self-impact/queue race model','Capacity is visible-depth stress only',
                 'No multiple-testing-adjusted significance; no annualization; no live orders']}
    for name,info in a['files'].items():assert sha(source/name)==info['sha256']
    write_json(out/'summary.json',summary)
    print(json.dumps({'training_champion':champion,'comparison':[{'family':r['family'],'train':r['train']['net_pnl'] if r['train'] else None,'test':r['test']['net_pnl'] if r['test'] else None,'trades':r['test']['trades'] if r['test'] else 0} for r in comparison],'checked_runs':checked},indent=2))


if __name__=='__main__':main()
