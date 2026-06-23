"""Probe Coinbase pagination and ordering behavior."""
import requests
from datetime import datetime, timezone

URL = "https://api.exchange.coinbase.com/products/BTC-USD/candles"

tests = [
    ("Test1: no start/end, 5m",          {"granularity": 300}),
    ("Test2: 1d window, 5m",             {"granularity": 300,  "start": "2024-10-01T00:00:00Z", "end": "2024-10-02T00:00:00Z"}),
    ("Test3: 3d window, 5m",             {"granularity": 300,  "start": "2024-10-01T00:00:00Z", "end": "2024-10-04T00:00:00Z"}),
    ("Test4: 30d window, 1h",            {"granularity": 3600, "start": "2024-10-01T00:00:00Z", "end": "2024-10-31T00:00:00Z"}),
    ("Test5: 25d window, 1h (cap test)", {"granularity": 3600, "start": "2024-10-01T00:00:00Z", "end": "2024-10-26T00:00:00Z"}),
]
for name, params in tests:
    try:
        r = requests.get(URL, params=params, timeout=15)
        d = r.json()
        if not isinstance(d, list):
            print(f"{name}: HTTP {r.status_code}, non-list resp: {d}")
            continue
        n = len(d)
        first_t = datetime.fromtimestamp(d[0][0],  tz=timezone.utc) if n else None
        last_t  = datetime.fromtimestamp(d[-1][0], tz=timezone.utc) if n else None
        order = "DESC (newest first)" if (n >= 2 and d[0][0] > d[-1][0]) else ("ASC (oldest first)" if n >= 2 else "n/a")
        print(f"{name}: HTTP {r.status_code}, n={n}, first={first_t}, last={last_t}, order={order}")
    except Exception as e:
        print(f"{name}: ERROR {e}")
