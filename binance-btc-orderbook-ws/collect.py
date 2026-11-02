"""Public spot top-five WebSocket capture; raw events plus honest 1s samples."""
import argparse
import asyncio
from decimal import Decimal
import json
from pathlib import Path
import time
import websockets


async def collect(out, duration):
    out.mkdir(parents=True, exist_ok=True)
    symbols = ['BTCUSDT', 'BNBUSDT']
    url = 'wss://stream.binance.com:443/stream?streams=' + '/'.join(
        s.lower() + '@depth5@100ms' for s in symbols)
    latest, counts = {}, dict.fromkeys(symbols, 0)
    connected = False
    start = time.monotonic()
    stop = start + duration
    def write(f, value):
        f.write(json.dumps(value, separators=(',', ':')) + '\n')
        f.flush()
    with (out / 'events.jsonl').open('a') as raw, (out / 'samples_1s.jsonl').open('a') as samples, (out / 'connection.jsonl').open('a') as log:
        async def receive():
            nonlocal connected
            while time.monotonic() < stop:
                try:
                    async with websockets.connect(url, open_timeout=10, close_timeout=2) as ws:
                        connected = True
                        latest.clear()
                        write(log, {'at_ms': time.time_ns() // 1000000, 'event': 'connected'})
                        while time.monotonic() < stop:
                            msg = json.loads(await asyncio.wait_for(ws.recv(), 5))
                            symbol = msg['stream'].split('@')[0].upper()
                            data = msg['data']
                            if symbol not in symbols:
                                raise ValueError('Unexpected symbol')
                            for side in ('bids', 'asks'):
                                levels = data[side]
                                prices = [Decimal(p) for p, q in levels]
                                if len(levels) != 5 or prices != sorted(prices, reverse=side == 'bids') or any(Decimal(p) <= 0 or Decimal(q) <= 0 for p, q in levels):
                                    raise ValueError('Invalid five-level book')
                            if Decimal(data['bids'][0][0]) >= Decimal(data['asks'][0][0]):
                                raise ValueError('Crossed book')
                            r = {'symbol': symbol, 'market': 'spot', 'received_at_ms': time.time_ns() // 1000000, **data}
                            latest[symbol] = (r, time.monotonic())
                            counts[symbol] += 1
                            write(raw, r)
                except Exception as e:
                    write(log, {'at_ms': time.time_ns() // 1000000, 'event': 'error', 'error': str(e), 'type': type(e).__name__})
                finally:
                    connected = False
                    latest.clear()
                await asyncio.sleep(1)
        task = asyncio.create_task(receive())
        try:
            for second in range(1, duration + 1):
                await asyncio.sleep(max(0, start + second - time.monotonic()))
                for symbol in symbols:
                    previous = latest.get(symbol)
                    age = (time.monotonic() - previous[1]) * 1000 if previous else None
                    status = 'ok' if connected and age is not None and age <= 1000 else ('stale' if connected and previous else 'missing')
                    write(samples, {'sample': second, 'symbol': symbol, 'sampled_at_ms': time.time_ns() // 1000000, 'scheduler_lag_ms': round((time.monotonic() - start - second) * 1000, 3), 'status': status, 'quote_age_ms': age, 'book': previous[0] if status == 'ok' else None})
                status_file = out / 'status.json'
                tmp = out / 'status.tmp'
                tmp.write_text(json.dumps({'state': 'running', 'elapsed_seconds': second, 'connected': connected, 'events': counts}))
                tmp.replace(status_file)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            (out / 'status.json').write_text(json.dumps({'state': 'stopped', 'events': counts, 'elapsed_seconds': time.monotonic() - start}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--seconds', type=int, default=1200)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(collect(args.out, args.seconds))
