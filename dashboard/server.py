#!/usr/bin/env python3
"""Local proxy server using yfinance with caching for speed."""

import gzip
import http.server
import http.cookiejar
import json
import os
import re
import socketserver
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

try:
    import yfinance as yf
except ImportError:
    import subprocess
    print('yfinance not found — installing now...')
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'yfinance'])
    import yfinance as yf
    print('yfinance installed.\n')

PORT = int(os.environ.get('PORT', 5000))

# All tickers from the portfolios
ALL_TICKERS = [
    'SPGP', 'IWF', 'IWY', 'SCHG', 'SPYG', 'MGK', 'VONG',
    'PFFD', 'ANGL', 'VWOB', 'FMHI', 'SRLN', 'ICVT',
    'TSLX', 'MAIN', 'ARCC', 'HTGC', 'CSWC', 'OBDC', 'GBDC', 'BXSL', 'TRIN', 'MSDL',
    'IEMG', 'VGK',
]

# ── Cache ─────────────────────────────────────────────────────────────────────
_cache = {}
_cache_lock = threading.Lock()


def _fetch_chart(ticker, params):
    """Fetch ticker data via yfinance and return in Yahoo Finance chart format."""
    t = yf.Ticker(ticker)

    if 'period1' in params and 'period2' in params:
        # Pass dates as plain strings — avoids tz-aware datetime issues in yfinance 1.x
        start = datetime.fromtimestamp(int(params['period1']), tz=timezone.utc).strftime('%Y-%m-%d')
        end   = datetime.fromtimestamp(int(params['period2']), tz=timezone.utc).strftime('%Y-%m-%d')
        hist  = t.history(start=start, end=end, interval='1d')
    else:
        # Fetch 2 days so we always have a previous-close bar available
        hist = t.history(period='2d', interval='1d')

    # Current price, open, and previous close from fast_info
    price          = None
    open_price     = None
    previous_close = None
    try:
        fi             = t.fast_info
        price          = float(fi.last_price)
        open_price     = float(fi.open)
        previous_close = float(fi.previous_close)
    except Exception:
        pass

    # Build parallel timestamp / close / open arrays
    timestamps = []
    closes     = []
    opens      = []
    for ts, row in hist.iterrows():
        timestamps.append(int(ts.timestamp()))
        c = row.get('Close')
        o = row.get('Open')
        closes.append(float(c) if c == c else None)   # NaN → None
        opens.append(float(o)  if o == o else None)

    # Fall back to last/second-to-last bar if fast_info didn't give us values
    if price is None and closes:
        price = closes[-1]
    if open_price is None and opens:
        open_price = opens[-1]
    if previous_close is None and len(closes) >= 2:
        previous_close = closes[-2]

    return {
        'chart': {
            'result': [{
                'meta': {
                    'regularMarketPrice':         price,
                    'regularMarketOpen':          open_price,
                    'regularMarketPreviousClose': previous_close,
                },
                'timestamp': timestamps,
                'indicators': {
                    'quote': [{'close': closes, 'open': opens}]
                },
            }],
            'error': None,
        }
    }


def _refresh_cache():
    """Fetch all tickers and update cache."""
    global _cache
    print('[cache] Refreshing all tickers with historical data...')

    # Calculate the date range the frontend uses (Dec 26, 2025 to today + 1 day)
    start_dt = datetime.strptime('2025-12-26', '%Y-%m-%d').replace(tzinfo=timezone.utc)
    end_dt   = datetime.now(tz=timezone.utc) + timedelta(days=1)

    for ticker in ALL_TICKERS:
        try:
            print(f'  {ticker}...', end=' ', flush=True)
            # Fetch full historical range for initial load
            params = {
                'period1': str(int(start_dt.timestamp())),
                'period2': str(int(end_dt.timestamp())),
            }
            data = _fetch_chart(ticker, params)
            with _cache_lock:
                _cache[ticker] = data
            price = data['chart']['result'][0]['meta']['regularMarketPrice']
            print(f'${price:.2f}' if price else 'OK')
        except Exception as e:
            print(f'ERROR: {e}', flush=True)
    print('[cache] Refresh complete.\n', flush=True)


def _cache_loop():
    """Background thread: refresh cache every 15 seconds."""
    while True:
        time.sleep(15)
        _refresh_cache()


class _Handler(http.server.SimpleHTTPRequestHandler):

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.startswith('/api/chart/'):
            ticker = parsed.path[len('/api/chart/'):]
            params = dict(urllib.parse.parse_qsl(parsed.query))
            self._proxy(ticker, params)
        else:
            super().do_GET()

    def _proxy(self, ticker, params):
        # Return cached data (works for both historical and live requests)
        with _cache_lock:
            if ticker in _cache:
                self._reply(200, json.dumps(_cache[ticker]).encode())
                return

        # Fallback: fetch live if not cached
        print(f'  {ticker}', end=' ... ', flush=True)
        try:
            data = _fetch_chart(ticker, params)
            price = data['chart']['result'][0]['meta']['regularMarketPrice']
            print(f'${price:.2f}' if price else 'OK')
            self._reply(200, json.dumps(data).encode())
        except Exception as e:
            print(f'ERROR: {e}')
            self._reply(500, json.dumps({'error': str(e)}).encode())

    def _reply(self, code, body):
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass   # suppress default request log


class ThreadedHTTPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    """Handle concurrent requests in threads."""
    daemon_threads = True
    allow_reuse_address = True


if __name__ == '__main__':
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    print('=' * 50)
    print(' Model Portfolio Dashboard')
    print('=' * 50)
    print()

    print('Pre-fetching all tickers (first time, takes ~30s)...')
    _refresh_cache()

    # Start background refresh thread
    cache_thread = threading.Thread(target=_cache_loop, daemon=True)
    cache_thread.start()

    with ThreadedHTTPServer(('', PORT), _Handler) as httpd:
        print(f'Open http://localhost:{PORT}/ in your browser')
        print('Press Ctrl+C to stop.\n')
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print('\nStopped.')
