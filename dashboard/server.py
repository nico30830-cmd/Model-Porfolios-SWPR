#!/usr/bin/env python3
"""Local proxy server for the Model Portfolio Dashboard.

Requirements:
    pip install yfinance

Usage:
    python server.py

Then open http://localhost:5000/ in your browser.
"""

import http.server
import json
import os
import socketserver
import sys
import urllib.parse
from datetime import datetime, timezone

try:
    import yfinance as yf
except ImportError:
    import subprocess
    print('yfinance not found — installing now...')
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'yfinance'])
    import yfinance as yf
    print('yfinance installed.\n')

PORT = int(os.environ.get('PORT', 5000))


def fetch_chart(ticker, params):
    """Fetch ticker data via yfinance and return in Yahoo Finance chart format."""
    t = yf.Ticker(ticker)

    if 'period1' in params and 'period2' in params:
        start = datetime.fromtimestamp(int(params['period1']), tz=timezone.utc)
        end   = datetime.fromtimestamp(int(params['period2']), tz=timezone.utc)
        hist  = t.history(start=start, end=end, interval='1d', auto_adjust=False)
    else:
        hist = t.history(period='1d', interval='1d', auto_adjust=False)

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
    if previous_close is None and closes and len(closes) >= 2:
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
        print(f'  {ticker}', end=' ... ', flush=True)
        try:
            data = fetch_chart(ticker, params)
            body = json.dumps(data).encode()
            price = data['chart']['result'][0]['meta']['regularMarketPrice']
            print(f'${price:.2f}' if price else 'OK')
            self._reply(200, body)
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


if __name__ == '__main__':
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    print('=' * 50)
    print(' Model Portfolio Dashboard')
    print('=' * 50)
    print()
    print('Testing connection (fetching SPY)...')
    try:
        fi = yf.Ticker('SPY').fast_info
        print(f'OK — SPY last price: ${fi.last_price:.2f}\n')
    except Exception as e:
        print(f'WARNING: {e}\n')

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(('', PORT), _Handler) as httpd:
        print(f'Open http://localhost:{PORT}/ in your browser')
        print('Press Ctrl+C to stop.\n')
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print('\nStopped.')
