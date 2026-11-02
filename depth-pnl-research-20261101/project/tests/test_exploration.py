import copy
import unittest
from depth_research.features import enrich, ofi_event
from depth_research.explore import predictive_samples, check_run
from depth_research.backtest import simulate
from depth_research.data import NS, units
from test_research import state


def row(t, bidq=10, askq=5, price=100):
    r=state(t,price=price)
    r['bids']=[(units(price),units(bidq))];r['asks']=[(units(price+1),units(askq))]
    r['bbands']=[bidq,0,0,0,0,0];r['abands']=[askq,0,0,0,0,0]
    r['B']=bidq;r['A']=askq;r['bid1']=bidq
    return r


class FeatureTests(unittest.TestCase):
    def test_ofi_add_cancel_and_quote_moves(self):
        a=row(0);b=row(.1,12,4)
        self.assertEqual(ofi_event(a,b),3)
        self.assertEqual(ofi_event(b,a),-3)
        # Both prices increase: new bid queue plus old ask queue.
        c=row(.2,8,9,101)
        self.assertEqual(ofi_event(b,c),12)

    def test_book_pressure_equivalent_to_qi(self):
        r=enrich([row(0,12,3)])[0]
        self.assertAlmostEqual(r['book_pressure']-r['mid'],r['qi']*(r['ask']-r['bid'])/2)

    def test_prefix_causal_no_input_mutation(self):
        rows=[row(i/10,10+i%3,5,100+i*.01) for i in range(80)]
        original=copy.deepcopy(rows)
        self.assertEqual(enrich(rows[:40]),enrich(rows)[:40])
        self.assertEqual(rows,original)

    def test_rolling_flow_expires_and_discontinuity_resets(self):
        rows=[row(0),row(.1,20),row(1.2,20)]
        r=enrich(rows)
        self.assertAlmostEqual(r[-1]['ofi1'],0)
        rows += [{'t':int(1.3*NS),'valid':False,'segment':1},row(1.4,20)]
        r=enrich(rows)
        self.assertIsNone(r[-1]['ofi1']);self.assertIsNone(r[-1]['momentum1'])
        self.assertIsNone(r[-1]['combo'])

    def test_prediction_uses_future_and_never_crosses_split(self):
        rows=enrich([row(i,price=100+i) for i in range(30)])
        before=predictive_samples(rows,0,10*NS,'qi',1)
        changed=copy.deepcopy(rows)
        for r in changed[10:]:r['mid']=1e6
        self.assertEqual(before,predictive_samples(changed,0,10*NS,'qi',1))
        self.assertEqual(len(before),9)
        self.assertGreater(before[0][1],0)

    def test_generic_signal_fee_and_latency_accounting(self):
        rows=enrich([row(i/10,20 if i<20 else 1,5,100+i*.01) for i in range(200)])
        r=simulate(rows,0,20*NS,{'entry':.4,'exit':0},units('.01'),signal_key='qi',fee_bps=1,
                   entry_sustain_ms=0,exit_sustain_ms=0,latency_ms=250,max_hold_s=5)
        self.assertEqual(len(r['trades']),1)
        check_run(r,0,20*NS,1,250,'.01')
        self.assertEqual(r['trades'][0]['exit_reason'],'imbalance_weak')


if __name__=='__main__':unittest.main()
