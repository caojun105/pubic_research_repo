import copy
import unittest
import numpy as np
from depth_research.refine import causal_extra, execute, measure, aggregate, candidates, choose
from depth_research.backtest import simulate
from depth_research.features import enrich
from depth_research.data import NS, units
from test_exploration import row


def fixture():
    states = enrich([row(i/10, 20 if (i//40)%2==0 else 1, 5, 100+i*.001) for i in range(400)])
    d = dict(t=np.array([r['t'] for r in states]), start=np.array(0), end=np.array(40*NS),
             mid=np.array([r['mid'] for r in states]), buy=np.array([r['ask'] for r in states]),
             sell=np.array([r['bid'] for r in states]),
             obi6=np.array([r['obi6'] for r in states]))
    return states, d


class RefinementTests(unittest.TestCase):
    def test_baseline_matches_original_including_fees_and_mid_marks(self):
        states, d = fixture(); cfg = dict(candidates()[0], max_hold=5)
        for latency in [100, 500]:
            for fee in [0, 1]:
                old = simulate(states, 0, 40*NS, {'entry':.4,'exit':0}, units('.01'),
                               signal_key='obi6', latency_ms=latency, fee_bps=fee, max_hold_s=5)
                new = execute(d, cfg, latency); stats = measure(new, fee)
                self.assertEqual(stats['trades'], old['summary']['trades'])
                self.assertGreater(stats['trades'], 0)
                self.assertAlmostEqual(stats['net_pnl'], old['summary']['net_pnl'])
                self.assertAlmostEqual(stats['mid_mdd'], old['summary']['mtm_max_drawdown'])

    def test_extra_features_are_prefix_causal(self):
        t = np.arange(500, dtype=np.int64)*NS//10
        mid = 100+np.sin(np.arange(500)/20)
        obi = np.sin(np.arange(500)/30)
        short = causal_extra(t[:400], mid[:400], obi[:400]); full = causal_extra(t, mid, obi)
        for key in short: np.testing.assert_allclose(short[key], full[key][:400], equal_nan=True)

    def test_stop_observed_then_delayed_and_cooldown_enforced(self):
        _, d = fixture(); d['obi6'][:] = 1
        d['buy'][:] = 100.; d['sell'][:] = 100.
        d['sell'][20:] = 99.
        cfg = dict(candidates()[0], max_hold=5, cooldown=3, stop_bps=5)
        run = execute(d, cfg, 500)
        self.assertEqual(run['trades'][0]['exit_reason'], 'stop')
        for r in run['trades']:
            self.assertGreaterEqual(d['t'][r['exit_index']]-d['t'][r['exit_signal_index']], NS//2)
        for a,b in zip(run['trades'], run['trades'][1:]):
            self.assertGreaterEqual(d['t'][b['entry_signal_index']]-d['t'][a['exit_index']], 3*NS)

    def test_liquidation_marks_charge_both_sides_and_aggregate_drawdown(self):
        _, d = fixture(); cfg = dict(candidates()[0], max_hold=5)
        run = execute(d, cfg, 100); s = measure(run, 1)
        curve = run['equity']-run['exposure']/10000
        expected = np.concatenate([curve, curve+s['net_pnl']])
        dd = (np.maximum.accumulate(np.maximum(expected,0))-expected).max()
        self.assertAlmostEqual(aggregate([s,s])['liquidation_mdd'], dd)
        self.assertTrue(np.all(run['exposure'][run['inventory']] > run['mid_exposure'][run['inventory']]))

    def test_test_rows_never_influence_selection_and_cash_fallback(self):
        plan = dict(candidates=[candidates()[0]], latencies_ms=[100,500])
        self.assertEqual(choose([],plan,1)['chosen'], 'cash')
        rows=[]
        for lat in [100,500]:
            for h in range(2):
                rows.append(dict(id='obi6_r02', split='validation', fee_bps=1,latency_ms=lat,
                    trades=15, net_pnl=2., gross_pnl=3.,turnover=10000.,liquidation_mdd=.1,
                    liquidation_mdd_pct=.01, curve_min=-.1,curve_max=2.))
        before=choose(rows,plan,1)
        rows.append(dict(rows[0], split='test',net_pnl=-1000000))
        self.assertEqual(before,choose(rows,plan,1))
        self.assertEqual(before['chosen'],'obi6_r02')


if __name__ == '__main__': unittest.main()
