import copy
import unittest
from depth_research.data import Book, NS, units, replay
from depth_research.backtest import simulate, walk


def snap(t=10, uid=100, session=1):
    return {'session':session,'available':{'local_mono_ns':t,'local_utc_ns':t},
            'snapshot':{'lastUpdateId':uid,'bids':[['100','10'],['90','10']],
                        'asks':[['101','10'],['110','10']]}}


def event(t, U, u, b=None, a=None, session=1):
    return {'session':session,'received':{'local_mono_ns':t,'local_utc_ns':t},
            'data':{'e':'depthUpdate','U':U,'u':u,'b':b or [],'a':a or []}}


def state(second, price=100, B=10, A=1, valid=True, segment=1):
    return {'t':int(second*NS),'utc':int(second*NS),'valid':valid,'segment':segment,
            'mid':price+0.5,'bid':price,'ask':price+1,'bid1':10,'B':B,'A':A,
            'bids':[(units(price),units(10))],'asks':[(units(price+1),units(10))]}


P = {'bid1_min':1,'ratio_in':2,'ratio_out':1}


class ReconstructionTests(unittest.TestCase):
    def test_snapshot_not_available_before_arrival_and_buffer(self):
        rows,c = replay([snap(10)], [event(5,101,101,b=[['100','8']]),event(20,102,102)], units('.01'))
        self.assertEqual(rows[0]['t'],10)
        self.assertEqual(rows[0]['bid1'],8)
        self.assertEqual(c['buffered'],1)

    def test_gap_requires_new_snapshot_even_if_missing_update_arrives_later(self):
        raw=[event(20,101,101),event(30,103,103),event(40,102,103),event(60,201,201)]
        rows,c=replay([snap(),snap(50,200)],raw,units('.01'))
        self.assertEqual([r['t'] for r in rows if r['valid']],[20,60])
        self.assertEqual(c['sequence_gap'],1)
        self.assertEqual(c['resyncs'],1)

    def test_stale_update_does_not_rewrite_depth(self):
        rows,c=replay([snap()],[event(20,101,101),event(30,100,101,b=[['100','99']])],units('.01'))
        self.assertEqual(rows[-1]['bid1'],10)
        self.assertEqual(c['stale'],1)

    def test_timeout_invalidates_until_resnapshot(self):
        rows,c=replay([snap()],[event(20,101,101),event(2*NS,102,102),event(2*NS+10,103,103)],units('.01'))
        self.assertEqual(c['timeout'],1)
        self.assertEqual(len([r for r in rows if r['valid']]),1)

    def test_crossed_and_session_changes_fail_closed(self):
        for e,reason in [(event(20,101,101,b=[['102','1']]),'invalid_book'),(event(20,101,101,session=2),'session_mismatch')]:
            rows,c=replay([snap()],[e],units('.01'))
            self.assertFalse(any(r['valid'] for r in rows))
            self.assertEqual(c[reason],1)

    def test_snapshot_bounds_not_extended_by_increment(self):
        b=Book(snap()['snapshot']); b.update({'u':101,'b':[['1','100']], 'a':[['200','100']]})
        self.assertEqual(b.floor,units(90)); self.assertEqual(b.ceiling,units(110))
        self.assertNotIn(units(1),b.b); self.assertNotIn(units(200),b.a)

    def test_exact_one_dollar_boundaries(self):
        s={'lastUpdateId':1,'bids':[['99.5','1'],['99','2'],['94','4'],['90','1']],
           'asks':[['100.5','1'],['101','3'],['106','4'],['110','1']]}
        r=Book(s).state(0,0,1,units('.01'))
        self.assertEqual(r['bbands'],[1,2,0,0,0,0])
        self.assertEqual(r['abands'],[1,3,0,0,0,0])
        self.assertEqual(r['B'],3); self.assertEqual(r['A'],4)

    def test_fractional_mid_exactness(self):
        self.assertEqual(units('0.00000001'),1)
        with self.assertRaises(ValueError):
            units('0.000000001')


class ExecutionTests(unittest.TestCase):
    def run_sim(self, rows, **kw):
        opts=dict(start=0,end=20*NS,params=P,qty=units('.01'),latency_ms=100,
                  max_hold_s=3,entry_sustain_ms=0,exit_sustain_ms=0,close_reserve_s=2)
        opts.update(kw)
        return simulate(rows,**opts)

    def test_walk_all_or_none_and_vwap(self):
        levels=[(units('100'),units('.006')),(units('102'),units('.004'))]
        self.assertAlmostEqual(walk(levels,units('.01'))[0],100.8)
        self.assertIsNone(walk(levels,units('.01000001')))

    def test_signal_delay_and_no_overlapping_positions(self):
        rows=[state(i/10, B=10 if i<10 else 0) for i in range(200)]
        run=self.run_sim(rows,latency_ms=500)
        self.assertEqual(len(run['trades']),1)
        t=run['trades'][0]
        self.assertGreaterEqual(t['entry_mono_ns']-t['entry_signal_mono_ns'],500_000_000)
        self.assertGreaterEqual(t['exit_mono_ns']-t['exit_signal_mono_ns'],500_000_000)
        self.assertEqual(t['entry_mono_ns'],500_000_000)

    def test_max_hold_independent_of_weak_signal_and_fees_reconcile(self):
        run=self.run_sim([state(i) for i in range(20)],fee_bps=5)
        self.assertTrue(run['trades'])
        t=run['trades'][0]
        self.assertEqual(t['exit_reason'],'max_hold')
        self.assertEqual(t['hold_seconds'],4)
        self.assertAlmostEqual(t['gross_pnl'],-.01)
        self.assertAlmostEqual(t['entry_fee'],.000505)
        self.assertAlmostEqual(t['exit_fee'],.0005)
        self.assertAlmostEqual(run['summary']['net_pnl'],run['equity'][-1]['equity'])

    def test_zero_latency_does_not_fill_on_signal_event(self):
        run=self.run_sim([state(i) for i in range(20)],latency_ms=0)
        self.assertEqual(run['trades'][0]['entry_mono_ns'],NS)

    def test_boundary_no_future_fill_or_position_silently_dropped(self):
        rows=[state(i) for i in range(20)]
        # Last scheduled sell signal at18; no later pre-boundary book available.
        run=self.run_sim(rows[:19]+[state(20,10000)],benchmark=True)
        self.assertFalse(run['summary']['valid_complete_result'])
        self.assertEqual(run['summary']['unresolved']['reason'],'no_executable_exit_before_boundary')
        self.assertEqual(run['trades'],[])

    def test_future_data_cannot_change_training(self):
        prefix=[state(i) for i in range(20)]
        a=self.run_sim(prefix)
        b=self.run_sim(prefix+[state(i,10000) for i in range(20,40)])
        self.assertEqual(a,b)

    def test_mark_to_market_drawdown_captures_open_loss(self):
        rows=[state(i,50 if i==2 else 100) for i in range(20)]
        run=self.run_sim(rows,benchmark=True)
        self.assertAlmostEqual(run['summary']['net_pnl'],-.01)
        self.assertGreater(run['summary']['mtm_max_drawdown'],.5)

    def test_insufficient_entry_depth_rejected(self):
        rows=[state(i) for i in range(20)]
        for r in rows:
            r['asks']=[(units(101),units('.001'))]
        run=self.run_sim(rows)
        self.assertEqual(run['summary']['trades'],0)
        self.assertGreater(run['summary']['rejections']['insufficient_depth_buy'],0)

    def test_insufficient_exit_preserves_position(self):
        rows=[state(i) for i in range(20)]
        for r in rows:
            r['bids']=[]
        run=self.run_sim(rows)
        self.assertFalse(run['summary']['valid_complete_result'])
        self.assertGreater(run['summary']['rejections']['insufficient_depth_sell'],0)

    def test_gap_with_open_position_not_fabricated_exit(self):
        rows=[state(0),state(1),state(2,valid=False),state(3,segment=2)]
        run=self.run_sim(rows)
        self.assertEqual(run['summary']['unresolved']['reason'],'book_discontinuity_with_position')
        self.assertEqual(run['trades'],[])

    def test_sustained_signal_resets_across_gap(self):
        rows=[state(0),state(.4,valid=False),state(.6,segment=2),state(.7,segment=2)]
        run=self.run_sim(rows,entry_sustain_ms=500)
        self.assertEqual(run['summary']['trades'],0)
        self.assertIsNone(run['summary']['unresolved'])

    def test_boundary_closes_inside_segment(self):
        run=self.run_sim([state(i) for i in range(21)],benchmark=True)
        self.assertEqual(run['trades'][0]['exit_mono_ns'],19*NS)
        self.assertEqual(run['trades'][0]['exit_reason'],'scheduled_boundary_exit')
        self.assertTrue(run['summary']['valid_complete_result'])

    def test_test_segment_starts_flat_and_cannot_inherit_signal(self):
        rows=[state(i) for i in range(40)]
        run=self.run_sim(rows,start=20*NS,end=40*NS,entry_sustain_ms=500)
        self.assertGreaterEqual(run['trades'][0]['entry_signal_mono_ns'],21*NS)


if __name__ == '__main__':
    unittest.main()
