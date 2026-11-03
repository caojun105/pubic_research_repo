"""Independent R02 saved-trade and aggregate accounting checks."""
import csv,json,math,argparse,collections,hashlib
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--results',default='experiments/r02/results');a=p.parse_args();root=Path(a.results)
s=json.loads((root/'summary.json').read_text());checks=0

def check(x,msg):
 global checks
 if not x:raise AssertionError(msg)
 checks+=1

def near(x,y,msg):check(math.isclose(float(x),float(y),abs_tol=1e-7,rel_tol=1e-10),msg)
check(s['frozen_parameters']=={**json.loads(Path('experiments/r01/results/selection.json').read_text())['parameters'],'buy_hold':json.loads(Path('experiments/r01/results/selection.json').read_text())['parameters']['original']},'Parameters retuned')
for name,h in s['source_sha256'].items():check(hashlib.sha256(Path(name).read_bytes()).hexdigest()==h,'Source changed')
common=set(s['common_clean_chunks']);audit=json.loads((root/'audit.json').read_text())
check(len(audit)==13,'Chunk count');check(all(x['clean_hour'] for x in audit if x['chunk'] in common),'Unclean cohort')
groups=collections.defaultdict(lambda:{'n':0,'net':0.,'gross':0.,'fees':0.,'last':-1})
with (root/'all_trades.csv').open() as f:
 for r in csv.DictReader(f):
  key=(r['chunk'],r['family'],int(r['latency_ms']),float(r['fee_bps']));g=groups[key]
  si,ei,so,eo=[int(r[k]) for k in ['entry_signal_mono_ns','entry_mono_ns','exit_signal_mono_ns','exit_mono_ns']]
  check(si<ei<=so<eo and ei>g['last'],'Trade order');g['last']=eo
  check(ei-si>=key[2]*1e6 and eo-so>=key[2]*1e6,'Latency')
  q=float(r['quantity_btc']);near(q,.01,'Quantity')
  gross=(float(r['exit_price'])-float(r['entry_price']))*q
  fee=(float(r['exit_price'])+float(r['entry_price']))*q*key[3]/10000
  near(gross,r['gross_pnl'],'Gross');near(fee,float(r['entry_fee'])+float(r['exit_fee']),'Fee');near(gross-fee,r['net_pnl'],'Net')
  g['n']+=1;g['gross']+=gross;g['fees']+=fee;g['net']+=gross-fee
hourly=list(csv.DictReader((root/'hourly.csv').open()))
for r in hourly:
 key=(r['chunk'],r['family'],int(r['latency_ms']),float(r['fee_bps']));g=groups[key]
 near(g['n'],r['trades'],'Count');near(g['net'],r['net_pnl'],'Hourly total')
for r in s['aggregate']:
 rr=[x for x in hourly if x['chunk'] in common and x['family']==r['family'] and int(x['latency_ms'])==r['latency_ms'] and float(x['fee_bps'])==r['fee_bps']]
 check(len(rr)==r['hours']==len(common),'Cohort size')
 for k in ['trades','gross_pnl','net_pnl','fees']:near(sum(float(x[k]) for x in rr),r[k],'Aggregate '+k)
check(s['full_13h_result_valid']==(len(common)==13),'Completeness flag')
result={'passed':True,'checks':checks,'frozen_parameters_unchanged':True,'common_clean_hours':len(common)}
(root/'validation.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
