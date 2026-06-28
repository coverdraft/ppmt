#!/usr/bin/env python3
"""Extract the PROXY_ROUTE and KRAKEN_PROXY strings from fix_ppmt_v3_cors.py."""
import re
src = open('/home/z/my-project/scripts/fix_ppmt_v3_cors.py').read()

# Find PROXY_ROUTE = """..."""
m = re.search(r'PROXY_ROUTE\s*=\s*"""(.*?)"""', src, re.DOTALL)
proxy = m.group(1)
m = re.search(r'KRAKEN_PROXY\s*=\s*"""(.*?)"""', src, re.DOTALL)
kraken = m.group(1)

with open('/tmp/my-project-test/src/app/api/coingecko/markets/route.ts', 'w') as f:
    f.write(proxy)
with open('/tmp/my-project-test/src/app/api/kraken/ticker/route.ts', 'w') as f:
    f.write(kraken)

print(f"CoinGecko route: {len(proxy)} bytes")
print(f"Kraken route:    {len(kraken)} bytes")
