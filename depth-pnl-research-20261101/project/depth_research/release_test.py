"""Frozen R01 parameters on independently supplied hourly Release data."""
import argparse,bisect,collections,gc,json,math
from pathlib import Path
from .data import NS,SCALE,load,replay,sha,units,iso
from .features import enrich
from .backtest import simulate
from .explore import check_run
from .__main__ import write_json,write_csv

def main():
 p=argparse.ArgumentParser();p.add_argument('--release-dir',required=True);p.add_argument('--output',default='experiments/r02/results');args=p.parse_args()
 root=Path(args.release_dir).resolve();out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
 plan=json.loads(Path('experiments/r02/plan.json').read_text());manifest=json.loads((root/'MANIFEST.json').read_text())
 selection=json.loads(Path('experiments/r01/results/selection.json').read_text())['parameters']
 selection={**selection,'buy_hold':selection['original']}
 hourly=[];audits=[];trade_rows=[];agg_curves={};checked=0;previous_end=None
 kw={k:plan[k] for k in ['max_hold_s','entry_sustain_ms','exit_sustain_ms','close_reserve_s']}
 for chunk in sorted(manifest['chunks'],key=lambda x:x['chunk']):
  name=chunk['chunk'];folder=root/'extracted'/name
  hashes={}
  for f in chunk['files']:
   path=folder/f['path'];h=sha(path)
   assert path.stat().st_size==f['bytes'] and h==f['sha256'],str(path)
   hashes[f['path']]=h
  assert json.loads((folder/'status.json').read_text())['state']=='complete'
  depth=[];counts=collections.Counter();symbols=set();prev=None;backwards=0
  with (folder/'binance_raw.jsonl').open() as f:
   for line in f:
    r=json.loads(line);t=r['received']['local_mono_ns'];backwards+=prev is not None and t<prev;prev=t
    symbols.add(r['data']['s']);counts[r['data']['e']]+=1
    if r['data']['e']=='depthUpdate':depth.append(r)
  assert symbols=={'BTCUSDT'} and not backwards
  assert len(depth)==chunk['counts']['binance_depth_events']
  aligned=load(folder/'aligned_1s.jsonl');start=aligned[0]['aligned_mono_ns'];end=start+3600*NS
  assert len(aligned)==3600 and all(b['aligned_mono_ns']-a['aligned_mono_ns']==NS for a,b in zip(aligned,aligned[1:]))
  utc_start=aligned[0]['aligned_utc_ns'];utc_end=utc_start+3600*NS
  gap=None if previous_end is None else (utc_start-previous_end)/NS
  assert gap is None or gap>=0,'Overlapping schedules';previous_end=utc_end
  states,rc=replay(load(folder/'binance_snapshots.jsonl'),depth,units(plan['quantity_btc']),plan['max_receive_gap_ms']);del depth
  states=enrich(states);ts=[r['t'] for r in states]
  cross=collections.Counter();invalid=[r for r in states if start<=r['t']<end and not r['valid']]
  valid=[r for r in states if r['valid']]
  by_uid={r['uid']:r for r in valid}
  for a in aligned:
   i=bisect.bisect_right(ts,a['aligned_mono_ns'])-1;d=a['binance'].get('data')
   if i<0 or not states[i]['valid'] or not d or not d.get('valid'):cross['unavailable']+=1;continue
   b=states[i];cross['compared']+=1
   different=units(d['bids20'][0][0])!=round(b['bid']*SCALE) or units(d['asks20'][0][0])!=round(b['ask']*SCALE) or b['uid']!=d['lastUpdateId']
   cross['mismatch']+=different
   if different:
    exact=by_uid.get(d['lastUpdateId'])
    explained=(exact is not None and exact['t']<=d.get('available_mono_ns',-1)<=a['aligned_mono_ns']
      and units(d['bids20'][0][0])==round(exact['bid']*SCALE)
      and units(d['asks20'][0][0])==round(exact['ask']*SCALE))
    cross['publication_lag_explained' if explained else 'unexplained']+=1
  clean=bool(valid) and valid[0]['t']<=start and valid[-1]['t']>=end-NS and not invalid and not cross['unexplained']
  audit={'chunk':name,'start_utc':iso(utc_start),'end_utc':iso(utc_end),'gap_before_s':gap,'input_hashes':hashes,'raw_counts':dict(counts),'reconstruction':rc,'crosscheck':dict(cross),'clean_hour':clean,'first_valid_offset_s':(valid[0]['t']-start)/NS if valid else None,'last_valid_offset_s':(valid[-1]['t']-end)/NS if valid else None,'valid_seconds_span':(valid[-1]['t']-valid[0]['t'])/NS if valid else 0,'first_invalid':[{'utc':iso(r['utc']),'reason':r['reason']} for r in invalid[:3]]}
  audits.append(audit)
  for family,params in selection.items():
   if params is None:continue
   signal=None if family in ['original','buy_hold'] else family
   for latency in plan['latencies_ms']:
    for fee in plan['fees_bps']:
     run=simulate(states,start,end,params,units(plan['quantity_btc']),latency_ms=latency,fee_bps=fee,signal_key=signal,benchmark=family=='buy_hold',**kw)
     complete=run['summary']['valid_complete_result'];eligible=clean and complete
     if complete:check_run(run,start,end,fee,latency,plan['quantity_btc']);checked+=1
     label={'chunk':name,'family':family,'latency_ms':latency,'fee_bps':fee}
     s=run['summary'];hourly.append({**label,'eligible_common_hour':eligible,'clean_hour':clean,**s})
     for tr in run['trades']:trade_rows.append({**label,**tr})
     if eligible:
      key=(family,latency,fee)
      z=agg_curves.setdefault(key,{'equity':0.,'peak':0.,'dd':0.})
      for point in run['equity']:
       value=z['equity']+point['equity'];z['peak']=max(z['peak'],value);z['dd']=max(z['dd'],z['peak']-value)
      z['equity']+=s['net_pnl']
  print(json.dumps({'chunk':name,'clean':clean,'reconstruction':rc,'obi6_pnl':next(r['net_pnl'] for r in hourly if r['chunk']==name and r['family']=='obi6' and r['latency_ms']==100 and r['fee_bps']==0)},ensure_ascii=False),flush=True)
  del states,aligned,valid;gc.collect()
  write_json(out/'audit.json',audits);write_csv(out/'hourly.csv',hourly)
 # Use intersection of completed chunks across all fixed families/scenarios.
 common=[a['chunk'] for a in audits if a['clean_hour'] and all(r['valid_complete_result'] for r in hourly if r['chunk']==a['chunk'])]
 if any(r['eligible_common_hour'] and r['chunk'] not in common for r in hourly):raise AssertionError('Incomplete case requires common-cohort curve recomputation')
 aggregate=[]
 for family in selection:
  for lat in plan['latencies_ms']:
   for fee in plan['fees_bps']:
    rr=[r for r in hourly if r['chunk'] in common and r['family']==family and r['latency_ms']==lat and r['fee_bps']==fee]
    trades=[r for r in trade_rows if r['chunk'] in common and r['family']==family and r['latency_ms']==lat and r['fee_bps']==fee]
    gross=sum(r['gross_pnl'] for r in rr);net=sum(r['net_pnl'] for r in rr);turn=sum(r['turnover_quote'] for r in rr)
    pnls=sorted([r['net_pnl'] for r in trades],reverse=True)
    aggregate.append({'family':family,'latency_ms':lat,'fee_bps':fee,'hours':len(rr),'trades':len(trades),'net_pnl':net,'gross_pnl':gross,'fees':sum(r['fees'] for r in rr),'win_rate':sum(v>1e-10 for v in pnls)/len(pnls) if pnls else None,'positive_hours':sum(r['net_pnl']>0 for r in rr),'worst_hour':min((r['net_pnl'] for r in rr),default=None),'best_hour':max((r['net_pnl'] for r in rr),default=None),'drawdown_common_hours':agg_curves.get((family,lat,fee),{}).get('dd'),'break_even_fee_bps':gross/turn*10000 if turn else None,'top3_pnl':sum(pnls[:3]),'without_top3':sum(pnls[3:]),'turnover':turn})
 write_csv(out/'aggregate.csv',aggregate);write_csv(out/'all_trades.csv',trade_rows)
 for a in audits:
  for n,h in a['input_hashes'].items():assert sha(root/'extracted'/a['chunk']/n)==h
 summary={'experiment':'R02','plan':plan,'frozen_parameters':selection,'release_manifest_sha256':sha(root/'MANIFEST.json'),'selection_sha256':sha('experiments/r01/results/selection.json'),'plan_sha256':sha('experiments/r02/plan.json'),'source_sha256':{str(p):sha(p) for p in Path('depth_research').glob('*.py')},'total_chunks':len(audits),'common_clean_chunks':common,'excluded_chunks':[a['chunk'] for a in audits if a['chunk'] not in common],'full_13h_result_valid':len(common)==len(audits),'checked_complete_runs':checked,'aggregate':aggregate}
 write_json(out/'summary.json',summary)
 print(json.dumps({'common_hours':len(common),'excluded':summary['excluded_chunks'],'baseline':[r for r in aggregate if r['latency_ms']==100 and r['fee_bps']==0]},indent=2),flush=True)

if __name__=='__main__':main()
