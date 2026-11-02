"""Independent reconciliation of R01 saved outputs (no backtest imports)."""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path

p=argparse.ArgumentParser();p.add_argument('--results',default='experiments/r01/results');p.add_argument('--data',required=True)
a=p.parse_args();root=Path(a.results);summary=json.loads((root/'summary.json').read_text());plan=summary['plan']
checks=0

def check(ok,msg):
    global checks
    if not ok:raise AssertionError(msg)
    checks+=1

def near(x,y,msg):check(math.isclose(float(x),float(y),rel_tol=1e-10,abs_tol=1e-8),msg)
def rows(f):return list(csv.DictReader((root/f).open()))
def digest(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()

for name,r in json.loads((root/'audit.json').read_text())['files'].items():check(digest(Path(a.data)/name)==r['sha256'],'input hash')
for name,h in summary['source_sha256'].items():check(digest(name)==h,'code hash')
check(digest('experiments/r01/plan.json')==summary['plan_sha256'],'plan hash')
lo,hi=summary['window']['split'],summary['window']['end']

def trades(filename,expected,fee,latency):
    rr=rows(filename);prev=lo-1
    for r in rr:
        si,ei,so,eo=[int(r[k]) for k in ['entry_signal_mono_ns','entry_mono_ns','exit_signal_mono_ns','exit_mono_ns']]
        check(lo<=si<ei<=so<eo<hi and ei>prev,'trade chronology');prev=eo
        check(ei-si>=latency*1e6 and eo-so>=latency*1e6,'latency')
        q=float(r['quantity_btc']);near(q,plan['quantity_btc'],'quantity')
        gross=(float(r['exit_price'])-float(r['entry_price']))*q
        fees=(float(r['exit_price'])+float(r['entry_price']))*q*fee/10000
        near(gross,r['gross_pnl'],'gross');near(fees,float(r['entry_fee'])+float(r['exit_fee']),'fees')
        near(gross-fees,r['net_pnl'],'net')
    near(sum(float(r['net_pnl']) for r in rr),expected['net_pnl'],'total')
    check(len(rr)==int(expected['trades']),'count')

lookup={r['family']:r for r in summary['comparison']}
grid=rows('training_grid.csv')
for key in plan['families']:
    eligible=[r for r in grid if r['family']==key and int(r['trades'])>=5 and float(r['net_pnl'])>0]
    if eligible:
        best=max(eligible,key=lambda r:float(r['net_pnl']))
        near(lookup[key]['selected']['entry'],best['entry'],'training selection')
    else:check(lookup[key]['selected'] is None,'no trade selection')
for r in summary['comparison']:
    if r['test'] is not None:trades(r['family']+'_test_trades.csv',r['test'],0,100)
for r in rows('sensitivity.csv'):
    trades(f"{r['family']}_fee{r['fee_bps']}_lat{r['latency_ms']}_trades.csv",r,float(r['fee_bps']),float(r['latency_ms']))
peak={};dd={};last={}
for r in rows('baseline_equity.csv'):
    k=r['family'];v=float(r['equity']);peak[k]=max(peak.get(k,0),v);dd[k]=max(dd.get(k,0),peak[k]-v)
    near(r['drawdown'],peak[k]-v,'point drawdown');last[k]=r
for k,r in last.items():
    near(r['equity'],lookup[k]['test']['net_pnl'],'terminal equity')
    near(dd[k],lookup[k]['test']['mtm_max_drawdown'],'max drawdown')
    near(r['position_btc'],0,'flat terminal')
# Confirm shared-execution extension preserved the original archived result.
old=json.loads(Path('results/summary.json').read_text())
for key in ['net_pnl','trades','mtm_max_drawdown','turnover_quote']:
    near(lookup['original']['test'][key],old['test'][key],'original regression '+key)
result={'passed':True,'checks':checks,'inputs_unchanged':True,'original_regression_passed':True}
(root/'validation.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
