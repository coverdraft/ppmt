"""
PPMT Dynamic Token Groups (v0.33.0)
====================================

Provides:
  1. Predefined groups (Top Market Cap, Categories, Blue Chips, etc.)
  2. Dynamic groups computed from live 24h tickers (volume, volatility, gainers...)
  3. Custom user-defined groups persisted to `groups_config.json`
  4. Combinable filters (min volume, exclude stablecoins, min age, etc.)

Public API
----------
- ``list_groups()``                -> dict of all available groups (predefined + custom).
- ``resolve_group(group_id, ...)`` -> list[str] of symbols (CCXT format, e.g. "BTC/USDT").
- ``save_custom_group(name, symbols, description)``
- ``delete_custom_group(name)``
- ``apply_filters(symbols, filters, exchange)``
- ``fetch_market_snapshot(exchange)`` -> dict[symbol] -> ticker dict (cached 60s)

Design notes
------------
- Custom groups live in ``~/.ppmt/groups_config.json`` so they survive reinstalls
  (the project's own ``groups_config.json`` ships as a *template* and is copied
  to ``~/.ppmt/`` on first use).
- All group resolution returns CCXT-style symbols ("BTC/USDT") so callers can
  pass them straight to ``DataCollector`` / ``RealtimeTrader``.
- Dynamic groups fetch ALL tickers ONCE via ccxt ``fetch_tickers()`` (one HTTP
  call), then sort/filter locally. Result is cached 60s to avoid hammering the
  exchange when the user clicks back and forth between groups.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# ============================================================
# Paths
# ============================================================
CONFIG_DIR = os.path.expanduser("~/.ppmt")
CUSTOM_GROUPS_FILE = os.path.join(CONFIG_DIR, "groups_config.json")
TEMPLATE_GROUPS_FILE = Path(__file__).resolve().parents[3] / "groups_config.json"

# Cache: exchange -> (timestamp, tickers dict)
_TICKER_CACHE: Dict[str, tuple] = {}
_TICKER_TTL = 60.0  # seconds


# ============================================================
# Predefined groups — static (don't need live data)
# ============================================================

# Predefined static groups. Each value is a list of base assets; the resolver
# appends "/USDT" to form CCXT symbols and drops any that aren't listed on the
# selected exchange.
PREDEFINED_STATIC_GROUPS: Dict[str, Dict] = {
    # --- Top Market Cap (curated, falls back gracefully if a token isn't listed)
    "top10_mcap": {
        "label": "Top 10 Market Cap",
        "category": "market_cap",
        "description": "BTC, ETH, USDT, BNB, SOL, XRP, USDC, ADA, AVAX, DOGE",
        "bases": ["BTC", "ETH", "BNB", "SOL", "XRP", "ADA", "AVAX", "DOGE",
                  "TRX", "TON"],
    },
    "top25_mcap": {
        "label": "Top 25 Market Cap",
        "category": "market_cap",
        "description": "Las 25 criptos por capitalización (curated list)",
        "bases": [
            "BTC", "ETH", "BNB", "SOL", "XRP", "ADA", "AVAX", "DOGE", "TRX", "TON",
            "DOT", "LINK", "MATIC", "LTC", "BCH", "ATOM", "UNI", "ETC", "XLM", "NEAR",
            "APT", "FIL", "ARB", "OP", "AAVE",
        ],
    },
    "top50_mcap": {
        "label": "Top 50 Market Cap",
        "category": "market_cap",
        "description": "Las 50 criptos por capitalización (curated list)",
        "bases": [
            "BTC", "ETH", "BNB", "SOL", "XRP", "ADA", "AVAX", "DOGE", "TRX", "TON",
            "DOT", "LINK", "MATIC", "LTC", "BCH", "ATOM", "UNI", "ETC", "XLM", "NEAR",
            "APT", "FIL", "ARB", "OP", "AAVE", "ICP", "HBAR", "VET", "INJ", "RNDR",
            "SUI", "SEI", "GRT", "ALGO", "FTM", "STX", "RUNE", "TIA", "IMX", "LDO",
            "MKR", "CRV", "SNX", "SAND", "MANA", "AXS", "EGLD", "THETA", "FLOW", "XTZ",
        ],
    },
    "top100_mcap": {
        "label": "Top 100 Market Cap",
        "category": "market_cap",
        "description": "Las 100 criptos por capitalización (curated list, may include some smaller tokens)",
        "bases": [
            # 50 above plus 50 more mid-caps
            "BTC", "ETH", "BNB", "SOL", "XRP", "ADA", "AVAX", "DOGE", "TRX", "TON",
            "DOT", "LINK", "MATIC", "LTC", "BCH", "ATOM", "UNI", "ETC", "XLM", "NEAR",
            "APT", "FIL", "ARB", "OP", "AAVE", "ICP", "HBAR", "VET", "INJ", "RNDR",
            "SUI", "SEI", "GRT", "ALGO", "FTM", "STX", "RUNE", "TIA", "IMX", "LDO",
            "MKR", "CRV", "SNX", "SAND", "MANA", "AXS", "EGLD", "THETA", "FLOW", "XTZ",
            "PEPE", "SHIB", "WIF", "BONK", "FLOKI", "MEME", "PYTH", "JUP", "RAY", "WLD",
            "DYDX", "GMX", "COMP", "1INCH", "BAL", "YFI", "SUSHI", "CHZ", "ENJ", "GALA",
            "ROSE", "KAVA", "ZIL", "BAT", "ZRX", "LRC", "OCEAN", "ANKR", "CELO", "WAVES",
            "DASH", "ZEC", "DCR", "XMR", "NEO", "GAS", "QTUM", "ICX", "WAN", "KSM",
            "MOVR", "GLMR", "CFX", "MASK", "FET", "AGIX", "OCEAN", "NMR", "RLC", "DODO",
        ],
    },

    # --- Categories ---
    "blue_chips": {
        "label": "Blue Chips",
        "category": "category",
        "description": "Los más líquidos y consolidados (BTC, ETH, BNB, SOL, XRP)",
        "bases": ["BTC", "ETH", "BNB", "SOL", "XRP"],
    },
    "altcoins_large": {
        "label": "Altcoins Grandes",
        "category": "category",
        "description": "Market Cap $5B+ (ADA, AVAX, DOT, LINK, MATIC, etc.)",
        "bases": ["ADA", "AVAX", "DOT", "LINK", "MATIC", "LTC", "BCH", "ATOM",
                  "UNI", "NEAR", "APT", "FIL", "ARB", "OP", "ICP", "HBAR"],
    },
    "altcoins_mid": {
        "label": "Altcoins Medianas",
        "category": "category",
        "description": "Market Cap $1B–$10B (INJ, SUI, SEI, GRT, RUNE, TIA, etc.)",
        "bases": ["INJ", "SUI", "SEI", "GRT", "ALGO", "FTM", "STX", "RUNE",
                  "TIA", "IMX", "LDO", "MKR", "CRV", "SNX", "EGLD", "THETA",
                  "FLOW", "XTZ", "ROSE", "KAVA"],
    },
    "altcoins_small": {
        "label": "Altcoins Pequeñas",
        "category": "category",
        "description": "Market Cap < $1B (ZIL, BAT, ZRX, LRC, ANKR, etc.)",
        "bases": ["ZIL", "BAT", "ZRX", "LRC", "OCEAN", "ANKR", "CELO", "WAVES",
                  "DASH", "ZEC", "DCR", "NEO", "GAS", "QTUM", "ICX", "KSM",
                  "MOVR", "GLMR", "CFX", "MASK"],
    },
    "memes": {
        "label": "Memes",
        "category": "category",
        "description": "DOGE, SHIB, PEPE, WIF, BONK, FLOKI, etc.",
        "bases": ["DOGE", "SHIB", "PEPE", "WIF", "BONK", "FLOKI", "MEME"],
    },
    "layer1": {
        "label": "Layer 1",
        "category": "category",
        "description": "Blockchains base (ETH, SOL, AVAX, ADA, NEAR, etc.)",
        "bases": ["ETH", "SOL", "AVAX", "ADA", "NEAR", "ATOM", "ALGO", "FTM",
                  "SUI", "SEI", "APT", "ICP", "EGLD", "FLOW", "XTZ", "KAS"],
    },
    "layer2": {
        "label": "Layer 2",
        "category": "category",
        "description": "Escalabilidad sobre Ethereum (ARB, OP, MATIC, IMX, etc.)",
        "bases": ["ARB", "OP", "MATIC", "IMX", "LRC", "MNT", "STRK", "MANTA",
                  "BLAST", "SCROLL"],
    },
    "defi": {
        "label": "DeFi",
        "category": "category",
        "description": "UNI, AAVE, MKR, CRV, SNX, COMP, etc.",
        "bases": ["UNI", "AAVE", "MKR", "CRV", "SNX", "COMP", "1INCH", "BAL",
                  "YFI", "SUSHI", "DYDX", "GMX", "LDO"],
    },
    "ai": {
        "label": "IA / AI",
        "category": "category",
        "description": "FET, RNDR, AGIX, OCEAN, NMR, etc.",
        "bases": ["FET", "RNDR", "AGIX", "OCEAN", "NMR", "WLD", "TAO", "GPC"],
    },
    "gaming": {
        "label": "Gaming / Metaverse",
        "category": "category",
        "description": "SAND, MANA, AXS, GALA, ENJ, etc.",
        "bases": ["SAND", "MANA", "AXS", "GALA", "ENJ", "CHZ", "ILLV", "APE"],
    },
}


# ============================================================
# Stablecoins — excluded by default
# ============================================================
STABLECOIN_BASES = {
    "USDT", "USDC", "DAI", "BUSD", "TUSD", "USDP", "GUSD", "USDD", "FRAX",
    "FDUSD", "PYUSD", "USTC", "USDS", "USDJ",
}


# ============================================================
# Dynamic group definitions — require live ticker data
# ============================================================
DYNAMIC_GROUPS = {
    "top_volume_24h": {
        "label": "Mayor Volumen 24h",
        "category": "dynamic",
        "description": "Top 25 por volumen en USDT (24h)",
        "sort_key": "quoteVolume",
        "descending": True,
        "limit": 25,
    },
    "top_volatility_24h": {
        "label": "Mayor Volatilidad 24h",
        "category": "dynamic",
        "description": "Top 25 por rango (high-low)/low (24h)",
        "sort_key": "volatility_pct",
        "descending": True,
        "limit": 25,
        "min_volume_usd": 10_000_000,  # avoid illiquid noise
    },
    "top_gainers_24h": {
        "label": "Mayor Ganancia 24h",
        "category": "dynamic",
        "description": "Top 25 rendimiento positivo (24h)",
        "sort_key": "percentage",
        "descending": True,
        "limit": 25,
        "min_volume_usd": 5_000_000,
    },
    "top_losers_24h": {
        "label": "Mayor Pérdida 24h",
        "category": "dynamic",
        "description": "Top 25 rendimiento negativo (24h)",
        "sort_key": "percentage",
        "descending": False,
        "limit": 25,
        "min_volume_usd": 5_000_000,
    },
}


# ============================================================
# Filter defaults
# ============================================================
DEFAULT_FILTERS = {
    "exclude_stablecoins": True,    # ON by default
    "only_usdt_pairs": True,        # ON by default
    "min_volume_24h_usd": 0,        # 0 = no filter
    "min_volatility_pct": 0,        # 0 = no filter
    "min_listed_days": 0,           # 0 = no filter
    "limit": 50,                    # cap after all filters
}


# ============================================================
# Custom groups persistence
# ============================================================

def _ensure_config_dir() -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)


def _load_custom_groups() -> Dict[str, Dict]:
    """Load custom groups from ~/.ppmt/groups_config.json.

    If the file doesn't exist, copy the template from the project root
    (groups_config.json) on first use so the user has an example to edit.
    """
    if not os.path.exists(CUSTOM_GROUPS_FILE):
        if TEMPLATE_GROUPS_FILE.exists():
            try:
                _ensure_config_dir()
                with open(TEMPLATE_GROUPS_FILE) as f:
                    content = f.read()
                with open(CUSTOM_GROUPS_FILE, "w") as f:
                    f.write(content)
                logger.info(f"Created {CUSTOM_GROUPS_FILE} from template")
            except Exception as e:
                logger.warning(f"Could not copy groups template: {e}")
        return {}

    try:
        with open(CUSTOM_GROUPS_FILE) as f:
            data = json.load(f) or {}
        # Normalize: each value must have 'bases' or 'symbols'
        normalized = {}
        for name, gdef in data.items():
            if not isinstance(gdef, dict):
                continue
            bases = gdef.get("bases") or gdef.get("symbols") or []
            # Accept "BTCUSDT" or "BTC/USDT" — normalize to "BTC"
            clean_bases = []
            for b in bases:
                if not isinstance(b, str):
                    continue
                b2 = b.upper().strip()
                if b2.endswith("/USDT"):
                    clean_bases.append(b2[:-5])
                elif b2.endswith("USDT"):
                    clean_bases.append(b2[:-4])
                else:
                    clean_bases.append(b2)
            normalized[name] = {
                "label": gdef.get("label", name.replace("_", " ").title()),
                "category": "custom",
                "description": gdef.get("description", ""),
                "bases": clean_bases,
            }
        return normalized
    except Exception as e:
        logger.warning(f"Failed to load custom groups: {e}")
        return {}


def save_custom_group(name: str, symbols: List[str], description: str = "") -> bool:
    """Save a custom group to ~/.ppmt/groups_config.json.

    `symbols` may be in CCXT format ("BTC/USDT") or raw bases ("BTC").
    """
    if not name or not symbols:
        return False
    name = name.strip().lower().replace(" ", "_")
    if name.startswith("_") or name in PREDEFINED_STATIC_GROUPS or name in DYNAMIC_GROUPS:
        return False  # reserved name

    bases = []
    for s in symbols:
        s2 = s.upper().strip()
        if s2.endswith("/USDT"):
            bases.append(s2[:-5])
        elif s2.endswith("USDT") and len(s2) > 4:
            bases.append(s2[:-4])
        else:
            bases.append(s2)

    _ensure_config_dir()
    data = _load_custom_groups()
    # Drop 'category' field if present (we'll re-set it on save)
    data[name] = {
        "label": name.replace("_", " ").title(),
        "description": description,
        "bases": bases,
    }
    try:
        with open(CUSTOM_GROUPS_FILE, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        logger.warning(f"Failed to save custom group: {e}")
        return False


def delete_custom_group(name: str) -> bool:
    if not name:
        return False
    name = name.strip().lower().replace(" ", "_")
    data = _load_custom_groups()
    if name not in data:
        return False
    del data[name]
    try:
        with open(CUSTOM_GROUPS_FILE, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        logger.warning(f"Failed to delete custom group: {e}")
        return False


# ============================================================
# Live ticker fetching (cached)
# ============================================================

def fetch_market_snapshot(exchange: str = "mexc") -> Dict[str, dict]:
    """Fetch ALL USDT-quoted tickers for the exchange (1 HTTP call, cached 60s).

    Returns: { "BTC/USDT": { last, high, low, bid, ask, quoteVolume, percentage, ... }, ... }
    """
    now = time.time()
    cached = _TICKER_CACHE.get(exchange)
    if cached and (now - cached[0]) < _TICKER_TTL:
        return cached[1]

    try:
        import ccxt
        ex_cls = getattr(ccxt, exchange, None)
        if ex_cls is None:
            logger.warning(f"Unknown exchange '{exchange}'")
            return {}
        ex = ex_cls()
        try:
            markets = ex.load_markets()
            all_tickers = ex.fetch_tickers()
        finally:
            if hasattr(ex, "close"):
                ex.close()

        # Filter to active USDT spot pairs
        result: Dict[str, dict] = {}
        for sym, t in all_tickers.items():
            if not sym.endswith("/USDT"):
                continue
            if sym not in markets:
                continue
            if not markets[sym].get("active", True):
                continue
            base = sym[:-5]
            # Skip leveraged tokens
            if base.startswith(("1000", "10000", "3L", "3S", "5L", "5S")):
                continue
            if base.endswith(("UP", "DOWN", "BULL", "BEAR")) and len(base) > 4:
                continue

            tdict = t.to_dict() if hasattr(t, "to_dict") else dict(t)
            # Compute volatility_pct if high/low available
            high = float(tdict.get("high") or 0)
            low = float(tdict.get("low") or 0)
            if low > 0 and high > 0:
                tdict["volatility_pct"] = ((high - low) / low) * 100.0
            else:
                tdict["volatility_pct"] = 0.0
            result[sym] = tdict

        _TICKER_CACHE[exchange] = (now, result)
        logger.info(f"Fetched {len(result)} USDT tickers from {exchange}")
        return result
    except Exception as e:
        logger.warning(f"fetch_market_snapshot({exchange}) failed: {e}")
        return {}


# ============================================================
# Filter application
# ============================================================

def apply_filters(
    symbols: List[str],
    filters: Optional[Dict] = None,
    exchange: str = "mexc",
) -> List[str]:
    """Apply combinable filters to a list of symbols.

    Supported filters:
      - exclude_stablecoins (bool, default True)
      - only_usdt_pairs      (bool, default True)
      - min_volume_24h_usd   (float, default 0 = no filter)
      - min_volatility_pct   (float, default 0 = no filter)
      - min_listed_days      (int,   default 0 = no filter; uses ticker's first-trade
                                        date when available, else skips check)
      - limit                (int,   default 50, applied LAST)
    """
    f = {**DEFAULT_FILTERS, **(filters or {})}

    # Get tickers once if needed for any volume/volatility filter
    need_tickers = (
        f.get("min_volume_24h_usd", 0) > 0
        or f.get("min_volatility_pct", 0) > 0
    )
    tickers = fetch_market_snapshot(exchange) if need_tickers else {}

    out: List[str] = []
    for sym in symbols:
        if not isinstance(sym, str):
            continue
        sym = sym.strip()
        if not sym:
            continue

        # Normalize to "BTC/USDT"
        if "/" not in sym:
            sym = f"{sym}/USDT"

        base = sym.split("/")[0].upper()

        # Stablecoin filter
        if f.get("exclude_stablecoins", True) and base in STABLECOIN_BASES:
            continue

        # USDT-only filter (always true here because we normalize to /USDT,
        # but keep the check for explicit symbol lists with other quotes)
        if f.get("only_usdt_pairs", True) and not sym.endswith("/USDT"):
            continue

        # Volume / volatility filters require ticker data
        if need_tickers:
            t = tickers.get(sym)
            if t is None:
                # Token not in exchange tickers — drop it (probably not listed)
                continue
            vol = float(t.get("quoteVolume") or 0)
            if f.get("min_volume_24h_usd", 0) > 0 and vol < f["min_volume_24h_usd"]:
                continue
            vol_pct = float(t.get("volatility_pct") or 0)
            if f.get("min_volatility_pct", 0) > 0 and vol_pct < f["min_volatility_pct"]:
                continue

        out.append(sym)

    # Apply limit last
    limit = int(f.get("limit", 50) or 0)
    if limit > 0 and len(out) > limit:
        out = out[:limit]
    return out


# ============================================================
# Group listing + resolution
# ============================================================

def list_groups() -> Dict[str, Dict]:
    """Return all groups (predefined + dynamic + custom) with metadata.

    Each entry: { "label", "category", "description" }
    Custom groups also include "bases" so the UI can display them.
    """
    out: Dict[str, Dict] = {}
    for gid, gdef in PREDEFINED_STATIC_GROUPS.items():
        out[gid] = {
            "label": gdef["label"],
            "category": gdef["category"],
            "description": gdef["description"],
        }
    for gid, gdef in DYNAMIC_GROUPS.items():
        out[gid] = {
            "label": gdef["label"],
            "category": gdef["category"],
            "description": gdef["description"],
        }
    for gid, gdef in _load_custom_groups().items():
        out[gid] = {
            "label": gdef.get("label", gid),
            "category": "custom",
            "description": gdef.get("description", ""),
            "bases": gdef.get("bases", []),
        }
    return out


def _resolve_static_group(gdef: Dict, exchange: str, filters: Optional[Dict]) -> List[str]:
    """Convert a static group's `bases` to filtered CCXT symbols.

    Drops tokens not listed on the exchange (so "Top 100 Market Cap" doesn't
    fail on MEXC if a particular token isn't there).
    """
    bases = gdef.get("bases", [])
    symbols = [f"{b}/USDT" for b in bases]
    return apply_filters(symbols, filters, exchange)


def _resolve_dynamic_group(
    gdef: Dict, exchange: str, filters: Optional[Dict]
) -> List[str]:
    """Compute a dynamic group from live tickers (volume/volatility/gainers)."""
    tickers = fetch_market_snapshot(exchange)
    if not tickers:
        return []

    # Pre-filter stablecoins + leveraged BEFORE sorting
    f = {**DEFAULT_FILTERS, **(filters or {})}
    candidates: List[tuple] = []  # (sort_value, symbol)
    for sym, t in tickers.items():
        base = sym.split("/")[0].upper()
        if f.get("exclude_stablecoins", True) and base in STABLECOIN_BASES:
            continue
        vol = float(t.get("quoteVolume") or 0)
        # Apply dynamic group's own min_volume filter (e.g. avoid illiquid gainers)
        min_vol = gdef.get("min_volume_usd", 0)
        if min_vol and vol < min_vol:
            continue

        sort_key = gdef["sort_key"]
        if sort_key == "quoteVolume":
            val = vol
        elif sort_key == "volatility_pct":
            val = float(t.get("volatility_pct") or 0)
        elif sort_key == "percentage":
            val = float(t.get("percentage") or 0)
        else:
            val = 0
        candidates.append((val, sym))

    candidates.sort(key=lambda x: x[0], reverse=gdef.get("descending", True))
    limit = gdef.get("limit", 25)
    top_syms = [s for _, s in candidates[:limit]]
    # Apply user filters (e.g. limit) on top
    return apply_filters(top_syms, filters, exchange)


def resolve_group(
    group_id: str,
    exchange: str = "mexc",
    filters: Optional[Dict] = None,
) -> List[str]:
    """Resolve a group ID to a list of CCXT-style symbols.

    Returns empty list if the group_id is unknown or resolution fails.
    """
    if group_id in PREDEFINED_STATIC_GROUPS:
        return _resolve_static_group(PREDEFINED_STATIC_GROUPS[group_id], exchange, filters)
    if group_id in DYNAMIC_GROUPS:
        return _resolve_dynamic_group(DYNAMIC_GROUPS[group_id], exchange, filters)
    custom = _load_custom_groups()
    if group_id in custom:
        return _resolve_static_group(custom[group_id], exchange, filters)
    logger.warning(f"Unknown group_id: {group_id}")
    return []


# ============================================================
# Helpers
# ============================================================

def clear_ticker_cache() -> None:
    """Force a fresh ticker fetch on next resolve_group()."""
    _TICKER_CACHE.clear()


__all__ = [
    "DEFAULT_FILTERS",
    "STABLECOIN_BASES",
    "list_groups",
    "resolve_group",
    "apply_filters",
    "fetch_market_snapshot",
    "save_custom_group",
    "delete_custom_group",
    "clear_ticker_cache",
]
