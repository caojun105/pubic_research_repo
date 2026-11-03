"""Independently reconcile R03 saved trades against cached reconstructed books.

Does not call the strategy simulator. Rebuilds every event's liquidation equity
from the trade ledger and checks every saved hourly and aggregate metric.
"""
import argparse
import collections
import csv
import json
import math
import sys
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from depth_research.data import NS, sha


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--experiment', default='experiments/r03')
    p.add_argument('--cache', default='/workspace/scratch/da9bc8c1119b/r03-cache')
    args = p.parse_args(); root = Path(args.experiment); out = root/'results'
    plan = json.loads((root/'plan.json').read_text())
    summary = json.loads((out/'summary.json').read_text())
    selection = json.loads((out/'selection.json').read_text())
    checks = 0
    def check(ok):
        nonlocal checks
        assert ok
        checks += 1
    def close(x,y): check(math.isclose(float(x),float(y),rel_tol=1e-9,abs_tol=1e-7))
    check(sha(root/'plan.json')==summary['plan_sha256']==selection['plan_sha256'])
    check(sha(out/'models.json')==selection['models_sha256'])
    for file,h in summary['source_sha256'].items(): check(sha(file)==h)
    grouped = collections.defaultdict(list)
    with (out/'trades.csv').open() as f:
        for r in csv.DictReader(f): grouped[(r['chunk'],r['id'],int(r['latency_ms']))].append(r)
    hourly = list(csv.DictReader((out/'hourly.csv').open()))
    aggregate_rows = list(csv.DictReader((out/'aggregate.csv').open()))
    full_curves = collections.defaultdict(list)
    full_pnls = collections.defaultdict(list)
    by_chunk = collections.defaultdict(list)
    for r in hourly: by_chunk[r['chunk']].append(r)
    run_count = trade_count = 0
    for chunk, records in by_chunk.items():
        with np.load(Path(args.cache)/(chunk+'.npz')) as z: d = {k:z[k] for k in z.files}
        t=d['t']; mask=(t>=int(d['start']))&(t<int(d['end']))
        chunk_runs = {(r['id'],int(r['latency_ms'])) for r in records}
        for ident,lat in sorted(chunk_runs):
            tt = grouped[(chunk,ident,lat)]; n=len(t)
            realized = np.zeros(n); turnover = np.zeros(n); held = np.zeros(n); exposure = np.zeros(n)
            previous = -1
            for tr in tt:
                si,ei,so,eo=[int(tr[k]) for k in ['entry_signal_index','entry_index','exit_signal_index','exit_index']]
                check(0<=si<ei<=so<eo<n and ei>previous)
                check(int(d['start'])<=t[si] and t[eo]<int(d['end']))
                check(ei==np.searchsorted(t,t[si]+lat*1_000_000))
                check(eo==np.searchsorted(t,t[so]+lat*1_000_000))
                close(tr['entry_price'],d['buy'][ei]); close(tr['exit_price'],d['sell'][eo])
                gross=(d['sell'][eo]-d['buy'][ei])*.01
                turn=(d['sell'][eo]+d['buy'][ei])*.01
                close(tr['gross_pnl'],gross);close(tr['turnover'],turn)
                close(tr['hold_seconds'],(t[eo]-t[ei])/NS)
                realized[eo]+=gross;turnover[eo]+=turn
                held[ei:eo]=(d['sell'][ei:eo]-d['buy'][ei])*.01
                exposure[ei:eo]=(d['sell'][ei:eo]+d['buy'][ei])*.01
                previous=eo;trade_count+=1
            realized=np.cumsum(realized);turnover=np.cumsum(turnover)
            for r in records:
                if r['id']!=ident or int(r['latency_ms'])!=lat:continue
                fee=float(r['fee_bps']); curve=(realized+held-(turnover+exposure)*fee/10000)[mask]
                pnls=[float(tr['gross_pnl'])-float(tr['turnover'])*fee/10000 for tr in tt]
                peak=np.maximum.accumulate(np.maximum(curve,0.))
                check(len(tt)==int(r['trades']))
                close(r['net_pnl'],sum(pnls));close(r['gross_pnl'],realized[-1]);close(r['turnover'],turnover[-1])
                close(r['fees'],turnover[-1]*fee/10000)
                close(curve[-1],sum(pnls));close(r['liquidation_mdd'],np.max(peak-curve))
                close(r['curve_min'],np.min(curve));close(r['curve_max'],np.max(curve))
                close(r['without_top3'],sum(sorted(pnls)[:-3]));close(r['return_pct'],sum(pnls)/10)
                for split in [r['split'],'all_seen']:
                    key=(split,ident,lat,fee)
                    full_curves[key].append(curve);full_pnls[key].extend(pnls)
            run_count+=1
    for r in aggregate_rows:
        key=(r['split'],r['id'],int(r['latency_ms']),float(r['fee_bps']))
        curves=full_curves[key]; pnls=full_pnls[key];offset=0.; joined=[]
        for curve in curves:joined.append(curve+offset);offset+=curve[-1]
        joined=np.concatenate(joined)
        close(r['net_pnl'],sum(pnls));check(int(r['trades'])==len(pnls))
        close(r['liquidation_mdd'],np.max(np.maximum.accumulate(np.maximum(joined,0.))-joined))
        close(r['without_top3'],sum(sorted(pnls)[:-3]))
        check(int(r['positive_hours'])==sum(c[-1]>0 for c in curves))
    check(run_count==summary['run_count']);check(trade_count==summary['verification']['trades_checked'])
    result=dict(passed=True, independent_checks=checks, reconstructed_runs=run_count,
                reconciled_trades=trade_count, all_event_liquidation_curves_reconciled=True,
                source='saved trades plus cached visible VWAP; no simulator calls')
    (out/'validation.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
