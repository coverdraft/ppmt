#!/usr/bin/env python3
"""Analyze the pasted Next.js dev log to evaluate system performance."""
import re
from pathlib import Path

log = Path("/home/z/my-project/upload/Pasted Content_1782616663980.txt").read_text()

# Extract all response times
times = [int(m) for m in re.findall(r'in (\d+)ms', log)]

# Identify cache hits (<50ms) vs misses (>=50ms)
cache_hits = [t for t in times if t < 50]
cache_misses = [t for t in times if t >= 50]

print(f"=== Log Analysis ===")
print(f"Total requests: {len(times)}")
print(f"Avg response time: {sum(times)/len(times):.0f} ms")
print(f"Min: {min(times)} ms / Max: {max(times)} ms")
print(f"Cache hits (<50ms): {len(cache_hits)} ({len(cache_hits)*100//len(times)}%)")
print(f"Cache misses (>=50ms): {len(cache_misses)} ({len(cache_misses)*100//len(times)}%)")
print()

# Pattern visualization
pattern = ''.join('H' if t < 50 else 'M' for t in times)
print(f"Pattern (H=hit, M=miss):")
print(pattern)
print()

# Count endpoints
endpoints = re.findall(r'GET (\S+)', log)
endpoints_clean = [e.split('?')[0] for e in endpoints]
from collections import Counter
print("Endpoint frequency:")
for ep, count in Counter(endpoints_clean).most_common():
    print(f"  {count:3d}x {ep}")
print()

# Median and percentiles
sorted_times = sorted(times)
n = len(sorted_times)
print(f"Percentiles:")
print(f"  p50: {sorted_times[n//2]} ms")
print(f"  p90: {sorted_times[int(n*0.9)]} ms")
print(f"  p99: {sorted_times[int(n*0.99)]} ms")

# Group consecutive misses (indicates cache resets)
print()
print("=== Cache reset analysis ===")
groups = []
cur = ''
for t in times:
    cur += 'H' if t < 50 else 'M'
# Find runs of M (misses)
import re as _re
miss_runs = [(m.start(), m.end()) for m in _re.finditer(r'M+', pattern)]
print(f"Number of miss-runs: {len(miss_runs)}")
print(f"Longest miss-run: {max((e-s for s,e in miss_runs), default=0)} consecutive misses")
print(f"First 10 miss-runs (start-end lengths): {[(e-s) for s,e in miss_runs[:10]]}")
