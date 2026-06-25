"""PPMT v7.5 Paper Trader — self-contained module.

Layout:
- features.py : v6 59-feature extractor (subset, offline)
- feed.py     : Bybit OHLCV live + historical fetcher
- model.py    : train fresh v6-LONG LightGBM on bootstrap data, save/load
- engine.py   : main loop: on 5m close → extract features → predict → log
- runner.py   : CLI entrypoint
"""
