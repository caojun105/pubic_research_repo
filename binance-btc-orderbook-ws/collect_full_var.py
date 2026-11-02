"""Bounded public market-data capture: Binance spot depth/trades + Var BTC perp.

Initial depth snapshot + all received deltas are retained for reconstruction.
One-second rows use causal as-of selection on a single host monotonic clock.
Var mark/index are separate from quantity-specific RFQ bid/ask; no trading.
"""
import argparse
import asyncio
from collections import Counter, deque
from datetime import datetime, timezone
from decimal import Decimal
import json
import math
import os
from pathlib import Path
import time

import aiohttp
import websockets

SCALE = 100000000
BN_WS = 'wss://stream.binance.com:443/stream?streams=btcusdt@depth@100ms/btcusdt@trade'
BN_REST = 'https://api.binance.com/api/v3'
VAR_WS = 'wss://omni-ws-server.prod.ap-northeast-1.variational.io/prices'
VAR_QUOTE = 'https://omni.variational.io/api/quotes/simple'
INSTRUMENT = dict(underlying='BTC', instrument_type='perpetual_future', settlement_asset='USDC', kind='crypto', funding_interval_s=3600)


def stamp():
    return {'local_utc_ns': time.time_ns(), 'local_mono_ns': time.monotonic_ns()}


def iso(ns):
    return datetime.fromtimestamp(ns / 1e9, timezone.utc).isoformat()


def units(s):
    value = Decimal(str(s)) * SCALE
    if not value.is_finite() or value != value.to_integral_value():
        raise ValueError('Invalid fixed-point value')
    return int(value)


def fmt(value):
    return f'{value // SCALE}.{value % SCALE:08d}'


def source_ms(value):
    if value is None:
        return None
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp() * 1000
    except (ValueError, TypeError):
        return None


def asof(history, cutoff):
    return next((r for r in reversed(history) if r['available_mono_ns'] <= cutoff), None)


class Book:
    def __init__(self, snap):
        self.bids = {units(p): units(q) for p, q in snap['bids'] if units(q) > 0}
        self.asks = {units(p): units(q) for p, q in snap['asks'] if units(q) > 0}
        self.uid = snap['lastUpdateId']
        self.floor = min(self.bids)
        self.ceiling = max(self.asks)

    def apply(self, event):
        if event['u'] <= self.uid:
            return False
        if event['U'] > self.uid + 1:
            raise ValueError('Depth sequence gap')
        for side, changes in [(self.bids, event['b']), (self.asks, event['a'])]:
            for p, q in changes:
                p, q = units(p), units(q)
                if p <= 0 or q < 0:
                    raise ValueError('Invalid depth value')
                if q == 0:
                    side.pop(p, None)
                else:
                    side[p] = q
        self.uid = event['u']
        if not self.bids or not self.asks or max(self.bids) >= min(self.asks):
            raise ValueError('Empty or crossed book')
        return True

    def view(self, trade):
        # Discard prices outside the proven initial snapshot coverage for aggregates.
        bp = sorted((p for p in self.bids if p >= self.floor), reverse=True)
        ap = sorted(p for p in self.asks if p <= self.ceiling)
        if not bp or not ap or bp[0] >= ap[0]:
            raise ValueError('No valid covered inside market')
        out = {'lastUpdateId': self.uid,
               'bids20': [[fmt(p), fmt(self.bids[p])] for p in bp[:20]],
               'asks20': [[fmt(p), fmt(self.asks[p])] for p in ap[:20]],
               'known_bid_levels': len(self.bids), 'known_ask_levels': len(self.asks),
               'proven_price_floor': fmt(self.floor), 'proven_price_ceiling': fmt(self.ceiling),
               'bands_1usd_each_side': None}
        if trade:
            center = units(trade['price'])
            complete = center - 6*SCALE >= self.floor and center + 6*SCALE <= self.ceiling
            bins = {'bids': [0]*6, 'asks': [0]*6}
            # Bids: (center-(k+1),center-k]; asks: [center+k,center+k+1).
            # Exact outer endpoint +/-6 is excluded consistently.
            for side, levels in [('bids', self.bids), ('asks', self.asks)]:
                for p, q in levels.items():
                    distance = center-p if side == 'bids' else p-center
                    if 0 <= distance < 6*SCALE:
                        bins[side][distance // SCALE] += q
            out['bands_1usd_each_side'] = {
                'center_last_trade': trade, 'complete_within_snapshot_bounds': complete,
                'bid_qty_btc': [fmt(q) for q in bins['bids']],
                'ask_qty_btc': [fmt(q) for q in bins['asks']],
                'bid_total_btc': fmt(sum(bins['bids'])), 'ask_total_btc': fmt(sum(bins['asks']))}
        return out


class Capture:
    def __init__(self, path, seconds):
        path.mkdir(parents=True, exist_ok=False)
        self.path, self.seconds = path, seconds
        self.files = {}
        self.hist = {k: deque(maxlen=1000) for k in ['binance', 'var_mark', 'var_quote']}
        self.counts = Counter()
        self.quality = Counter()
        self.ready = {k: asyncio.Event() for k in ['binance', 'var_mark']}
        self.start = stamp()
        self.session = 0
        self.latest_trade = None
        self.write('metadata', {'created_at': iso(self.start['local_utc_ns']), 'pid': os.getpid(),
            'planned_seconds': seconds, 'binance_market': 'spot BTCUSDT',
            'var_market': 'BTC USDC perpetual', 'var_quote_qty_btc': '0.01',
            'snapshot_limit_per_side': 5000, 'depth_update_ms': 100,
            'alignment': 'causal as-of by available_mono_ns on UTC-second grid; never nearest future',
            'freshness_ms': {'binance':1000,'var_mark':3000,'var_quote':1500},
            'var_source_age_limit_ms':5000, 'source_clock_offset_not_assumed_zero':True,
            'limitations':['5000-level snapshot bounds, not unlimited book',
                          'Var marks are not executable quotes',
                          'Var RFQ may be rate limited; no substitution for missing bid/ask',
                          'Source exchange clocks retained separately; alignment is local availability',
                          'USDT spot and USDC perpetual prices are different instruments']})

    def write(self, name, obj):
        if name not in self.files:
            self.files[name] = (self.path / (name+'.jsonl')).open('a', buffering=1)
        self.files[name].write(json.dumps(obj,separators=(',',':'))+'\n')

    def log(self, event, **extra):
        self.write('connections', {'event':event, **stamp(), **extra})

    def publish(self, name, obj):
        s=stamp()
        record={**obj,'available_utc_ns':s['local_utc_ns'],'available_mono_ns':s['local_mono_ns']}
        self.hist[name].append(record)
        return record

    async def bn(self):
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as http:
            while True:
                self.session += 1
                session = self.session
                task = None
                self.latest_trade = None
                self.publish('binance', {'valid':False,'reason':'synchronizing','session':session})
                try:
                    async with websockets.connect(BN_WS,open_timeout=15,close_timeout=2,max_queue=4096) as ws:
                        self.log('binance_connected', session=session)
                        queue=asyncio.Queue(maxsize=20000)
                        async def receive():
                            try:
                                while True:
                                    raw=await asyncio.wait_for(ws.recv(),10)
                                    received=stamp()
                                    d=json.loads(raw)
                                    data=d['data']
                                    self.write('binance_raw',{'session':session,'received':received,**d})
                                    if data.get('e')=='trade':
                                        self.counts['binance_trades']+=1
                                        self.latest_trade={'price':data['p'],'qty':data['q'],'trade_id':data['t'],
                                            'exchange_trade_ms':data['T'],'exchange_event_ms':data['E'],
                                            'received':received}
                                    elif data.get('e')=='depthUpdate':
                                        self.counts['binance_depth_events']+=1
                                        queue.put_nowait((data,received))
                            except BaseException as e:
                                # Consumer must learn of disconnect even while waiting for depth.
                                if not isinstance(e,asyncio.CancelledError):
                                    await queue.put(e)
                                raise
                        task=asyncio.create_task(receive())
                        first=await asyncio.wait_for(queue.get(),15)
                        if isinstance(first,BaseException): raise first
                        while True:
                            sent=stamp()
                            async with http.get(BN_REST+'/depth',params={'symbol':'BTCUSDT','limit':5000}) as response:
                                body=await response.read(); received=stamp()
                                if response.status!=200: raise RuntimeError('Binance snapshot HTTP '+str(response.status))
                                snap=json.loads(body)
                            self.write('binance_snapshots',{'session':session,'requested':sent,'received':received,'available':stamp(),'snapshot':snap})
                            self.counts['binance_snapshots']+=1
                            if snap['lastUpdateId'] >= first[0]['U']: break
                            await asyncio.sleep(1)
                        book=Book(snap)
                        item=first
                        while True:
                            if isinstance(item,BaseException): raise item
                            data,received=item
                            if book.apply(data):
                                view=book.view(self.latest_trade)
                                rec=self.publish('binance',{'valid':True,'session':session,
                                    'exchange_event_ms':data['E'],'received':received,**view})
                                self.counts['binance_applied']+=1
                                self.ready['binance'].set()
                            item=await asyncio.wait_for(queue.get(),10)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    self.counts['binance_errors']+=1
                    self.log('binance_error',error_type=type(e).__name__,error=str(e)[:180],session=session)
                finally:
                    self.publish('binance',{'valid':False,'reason':'disconnected','session':session})
                    if task:
                        task.cancel()
                        await asyncio.gather(task,return_exceptions=True)
                await asyncio.sleep(2)

    async def var_mark(self):
        while True:
            try:
                async with websockets.connect(VAR_WS,open_timeout=15,close_timeout=2) as ws:
                    await ws.send(json.dumps({'action':'subscribe','instruments':[{k:v for k,v in INSTRUMENT.items() if k!='kind'}]}))
                    self.log('var_mark_connected')
                    while True:
                        raw=await asyncio.wait_for(ws.recv(),15)
                        received=stamp()
                        d=json.loads(raw)
                        self.write('var_ws_raw',{'received':received,'message':d})
                        if d.get('channel')!='instrument_price:P-BTC-USDC-3600': continue
                        p=d['pricing']
                        if Decimal(p['price'])<=0: raise ValueError('Invalid mark')
                        rec=self.publish('var_mark',{'valid':True,'received':received,'pricing':p,
                            'exchange_timestamp_raw':p.get('timestamp'),'exchange_event_ms':source_ms(p.get('timestamp'))})
                        self.counts['var_mark_events']+=1
                        self.ready['var_mark'].set()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.counts['var_mark_errors']+=1
                self.log('var_mark_error',error_type=type(e).__name__,error=str(e)[:180])
            finally:
                self.publish('var_mark',{'valid':False,'reason':'disconnected'})
            await asyncio.sleep(2)

    async def var_quote(self):
        # Read only the existing site's cookies; never print, persist, or send them elsewhere.
        cookie_path=Path(os.environ.get('VAR_COOKIE_PATH', '~/.config/binance-depth-ws/variational_cf.json')).expanduser()
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as http:
            while True:
                delay=3
                sent=stamp()
                result={'valid':False,'requested':sent,'quote_qty_btc':'0.01'}
                try:
                    cookie=json.loads(cookie_path.read_text())
                    headers={'User-Agent':cookie.get('ua','Mozilla/5.0'), 'Origin':'https://omni.variational.io','Referer':'https://omni.variational.io/perpetual/BTC'}
                    async with http.post(VAR_QUOTE,json={'instrument':INSTRUMENT,'qty':'0.01'},
                                         cookies=cookie['cookies'],headers=headers,allow_redirects=False) as response:
                        body=await response.read(); received=stamp()
                        result.update(received=received,http_status=response.status,
                            duration_ms=(received['local_mono_ns']-sent['local_mono_ns'])/1e6)
                        if response.status==200:
                            d=json.loads(body)
                            bid,ask=Decimal(str(d['bid'])),Decimal(str(d['ask']))
                            if not bid.is_finite() or not ask.is_finite() or not 0<bid<ask:
                                raise ValueError('Invalid RFQ prices')
                            result.update(valid=True,quote={k:d.get(k) for k in ['bid','ask','mark_price','index_price','timestamp']},
                                          exchange_event_ms=source_ms(d.get('timestamp')))
                            self.counts['var_quotes_ok']+=1
                        else:
                            result['reason']='http_'+str(response.status)
                            self.counts['var_quote_http_'+str(response.status)]+=1
                            delay=60 if response.status in (429,403) else 10
                            retry=response.headers.get('Retry-After','')
                            if retry.isdigit(): delay=max(delay,int(retry))
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    result.update(reason=type(e).__name__)
                    self.counts['var_quote_errors']+=1
                    delay=10
                rec=self.publish('var_quote',result)
                self.write('var_quotes',rec)
                await asyncio.sleep(delay)

    async def clock_probe(self):
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as http:
            while True:
                sent=stamp()
                try:
                    async with http.get(BN_REST+'/time') as response:
                        body=await response.read(); received=stamp()
                        d=json.loads(body)
                        server=d['serverTime']
                        self.write('clock_probes',{'requested':sent,'received':received,'server_ms':server,
                            'server_minus_local_lower_ms':server-received['local_utc_ns']/1e6,
                            'server_minus_local_upper_ms':server-sent['local_utc_ns']/1e6,
                            'rtt_ms':(received['local_mono_ns']-sent['local_mono_ns'])/1e6})
                except Exception as e:
                    self.log('clock_probe_error',error_type=type(e).__name__)
                await asyncio.sleep(60)

    def status(self, state, **extra):
        d={'state':state,'pid':os.getpid(),'counts':dict(self.counts),'quality':dict(self.quality),
           'updated_at':iso(time.time_ns()),**extra}
        temp=self.path/'status.tmp'
        temp.write_text(json.dumps(d,indent=2))
        temp.replace(self.path/'status.json')

    async def run(self):
        self.status('warming_up')
        tasks=[asyncio.create_task(c) for c in [self.bn(),self.var_mark(),self.var_quote(),self.clock_probe()]]
        completed=False
        try:
            await asyncio.wait_for(asyncio.gather(*(r.wait() for r in self.ready.values())),60)
            anchor=stamp()
            first_utc=(anchor['local_utc_ns']//1000000000+1)*1000000000
            first_mono=anchor['local_mono_ns']+first_utc-anchor['local_utc_ns']
            timing={'first_sample_utc':iso(first_utc),'last_sample_utc':iso(first_utc+(self.seconds-1)*1000000000),
                    'anchor':anchor,'planned_rows':self.seconds}
            (self.path/'schedule.json').write_text(json.dumps(timing,indent=2))
            self.log('measurement_started',**timing)
            for i in range(self.seconds):
                cutoff=first_mono+i*1000000000
                utc=first_utc+i*1000000000
                await asyncio.sleep(max(0,(cutoff-time.monotonic_ns())/1e9))
                observed=stamp()
                drift=(observed['local_utc_ns']-anchor['local_utc_ns']-(observed['local_mono_ns']-anchor['local_mono_ns']))/1e6
                row={'sample':i+1,'aligned_utc_ns':utc,'aligned_mono_ns':cutoff,'aligned_utc':iso(utc),
                     'written':observed,'scheduler_lag_ms':(observed['local_mono_ns']-cutoff)/1e6,
                     'clock_drift_ms':drift}
                for name,limit in [('binance',1000),('var_mark',3000),('var_quote',1500)]:
                    r=asof(self.hist[name],cutoff)
                    age=(cutoff-r['available_mono_ns'])/1e6 if r else None
                    status='missing'
                    event_age=None
                    if r:
                        status='ok' if r.get('valid') and age<=limit else ('stale' if r.get('valid') else r.get('reason','invalid'))
                        if r.get('exchange_event_ms') is not None:
                            event_age=utc/1e6-r['exchange_event_ms']
                            if status=='ok' and (event_age>5000 or event_age < -1000): status='source_time_out_of_range'
                        if name=='binance' and status=='ok':
                            bands=r.get('bands_1usd_each_side')
                            if not bands or not bands['complete_within_snapshot_bounds']: status='range_incomplete'
                    row[name]={'status':status,'available_age_ms':age,'exchange_event_age_ms':event_age,'data':r}
                    self.quality[name+'_'+status]+=1
                bn,vm,vq=(row[k] for k in ['binance','var_mark','var_quote'])
                def pair(a,b):
                    skew=abs(a['data']['available_mono_ns']-b['data']['available_mono_ns'])/1e6 if a['data'] and b['data'] else None
                    return {'availability_skew_ms':skew,'usable':a['status']=='ok' and b['status']=='ok' and skew<=1000 and abs(drift)<=50 and row['scheduler_lag_ms']<=1000}
                row['binance_var_mark_alignment']=pair(bn,vm)
                row['binance_var_quote_alignment']=pair(bn,vq)
                self.quality['mark_pair_usable']+=int(row['binance_var_mark_alignment']['usable'])
                self.quality['quote_pair_usable']+=int(row['binance_var_quote_alignment']['usable'])
                self.write('aligned_1s',row)
                self.counts['aligned_rows']+=1
                if i%5==0: self.status('running',**timing)
            completed=True
        finally:
            for t in tasks:t.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
            self.status('complete' if completed else 'stopped_incomplete')
            for f in self.files.values(): f.close()


def selftest():
    book=Book({'lastUpdateId':10,'bids':[['100','2'],['90','5']],'asks':[['101','3'],['110','6']]})
    assert not book.apply({'U':9,'u':10,'b':[],'a':[]})
    assert book.apply({'U':10,'u':11,'b':[['100','0'],['99','4']],'a':[]})
    assert units('100') not in book.bids and book.bids[units('99')]==units('4')
    try: book.apply({'U':13,'u':14,'b':[],'a':[]})
    except ValueError: pass
    else: raise AssertionError('Gap must fail')
    view=book.view({'price':'100'})
    assert view['bands_1usd_each_side']['complete_within_snapshot_bounds']
    assert view['bands_1usd_each_side']['bid_qty_btc'][1]=='4.00000000'
    assert view['bands_1usd_each_side']['ask_qty_btc'][1]=='3.00000000'
    assert not book.view({'price':'108'})['bands_1usd_each_side']['complete_within_snapshot_bounds']
    assert asof([{'available_mono_ns':10},{'available_mono_ns':20}],15)['available_mono_ns']==10
    assert asof([{'available_mono_ns':10}],9) is None
    assert asof([{'available_mono_ns':10,'valid':True},{'available_mono_ns':20,'valid':False}],21)['valid'] is False
    print('PASS: depth replacement/deletion, duplicate rejection, gap detection, range coverage, causal alignment',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--seconds',type=int,default=3600)
    parser.add_argument('--out',type=Path)
    parser.add_argument('--selftest',action='store_true')
    args=parser.parse_args()
    if args.selftest:selftest()
    else:
        if not args.out or not 1<=args.seconds<=3600:parser.error('Require --out and 1..3600 seconds')
        asyncio.run(Capture(args.out,args.seconds).run())
