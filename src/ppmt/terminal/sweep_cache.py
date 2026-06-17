"""
PPMT Sweep Result Cache (v0.34.0)
=================================

Caché de resultados de validación por (symbol, tf) durante un Sweep All
Groups. Si BTC aparece en 5 grupos, se valida 1 sola vez y el resultado
se reutiliza para los 5.

TTL: 5 min por defecto (los datos de mercado pueden cambiar).
"""
from __future__ import annotations

import time
from typing import Dict, Optional, Tuple

_SWEEP_CACHE_TTL_SEC = 300  # 5 minutos


class SweepResultCache:
    """Caché de resultados de validación por (symbol, tf).

    Uso:
        cache = SweepResultCache()
        key = cache.make_key("BTC/USDT", "15m")
        cached = cache.get(key)
        if cached:
            # reutilizar resultado
            ...
        else:
            result = _run_validation(...)
            cache.set(key, result)
    """

    def __init__(self, ttl_sec: int = _SWEEP_CACHE_TTL_SEC):
        self._cache: Dict[str, Tuple[float, dict]] = {}
        self._ttl = ttl_sec

    @staticmethod
    def make_key(symbol: str, tf: str) -> str:
        """BTC/USDT + 15m -> 'BTC/USDT|15m'."""
        return f"{symbol}|{tf}"

    def get(self, key: str) -> Optional[dict]:
        entry = self._cache.get(key)
        if entry is None:
            return None
        ts, result = entry
        if time.time() - ts > self._ttl:
            del self._cache[key]
            return None
        return result

    def set(self, key: str, result: dict) -> None:
        self._cache[key] = (time.time(), result)

    def clear(self) -> None:
        self._cache.clear()

    def stats(self) -> dict:
        return {"entries": len(self._cache), "ttl_sec": self._ttl}
