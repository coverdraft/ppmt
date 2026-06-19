"""Sequential Trie Builder for PPMT Shared Pool Population.

Build order matters: BTC first (most data, stabilizes N1), then ETH,
then the rest by asset class. This ensures shared N1/N2 pools accumulate
data progressively.

Usage:
    python -m ppmt.data.sequential_builder
"""

import time
from ppmt.data.storage import PPMTStorage, UNIVERSAL_POOL_KEY, class_pool_key
from ppmt.engine.ppmt import PPMT
from ppmt.data.classifier import AssetClassifier


# Build order: blue_chip first, then by increasing specificity
BUILD_ORDER = [
    ("blue_chip", ["BTC/USDT", "ETH/USDT"]),
    ("large_cap", ["SOL/USDT", "BNB/USDT", "XRP/USDT"]),
    ("mid_cap", ["LINK/USDT", "AVAX/USDT", "DOT/USDT"]),
    ("defi", ["UNI/USDT", "AAVE/USDT"]),
    ("meme", ["DOGE/USDT", "PEPE/USDT", "WIF/USDT"]),
]


def build_all_tries(timeframe: str = "15m", pattern_length: int = 5, 
                    storage: PPMTStorage = None) -> dict:
    """Build tries sequentially for all tokens, populating shared pools.
    
    Args:
        timeframe: Timeframe to build
        pattern_length: Pattern length for trie construction
        storage: PPMTStorage instance (created if None)
    
    Returns:
        Dict with build stats and pool verification.
    """
    if storage is None:
        storage = PPMTStorage()
    
    classifier = AssetClassifier()
    stats = {
        'tokens_built': [],
        'pool_status': {},
        'errors': [],
    }
    
    for asset_class, symbols in BUILD_ORDER:
        for symbol in symbols:
            print(f"\n{'='*60}")
            print(f"Building: {symbol} ({asset_class}) @ {timeframe}")
            print(f"{'='*60}")
            
            try:
                # Load OHLCV data
                df = storage.load_ohlcv(symbol, timeframe)
                if df is None or len(df) < 100:
                    msg = f"{symbol}: insufficient data ({len(df) if df is not None else 0} rows)"
                    print(f"  SKIP: {msg}")
                    stats['errors'].append(msg)
                    continue
                
                # Get asset class info
                info = classifier.classify(symbol)
                
                # Create engine with storage attached
                engine = PPMT(
                    symbol=symbol,
                    asset_class=info.asset_class,
                    weight_profile=info.weight_profile,
                    timeframe=timeframe,
                )
                engine.attach_storage(storage)
                
                # Build
                count = engine.build(df, pattern_length=pattern_length)
                
                # Verify pools after build
                pool_status = engine.ensure_shared_pools(storage)
                
                print(f"  Patterns built: {count}")
                print(f"  N1 pool: {pool_status['n1_universal']['pattern_count']} patterns")
                print(f"  N2 pool ({asset_class}): {pool_status['n2_class']['pattern_count']} patterns")
                
                stats['tokens_built'].append({
                    'symbol': symbol,
                    'asset_class': asset_class,
                    'patterns': count,
                    'pool_status': pool_status,
                })
                
            except Exception as e:
                msg = f"{symbol}: {str(e)}"
                print(f"  ERROR: {msg}")
                stats['errors'].append(msg)
    
    # Final pool verification
    print(f"\n{'='*60}")
    print("FINAL POOL VERIFICATION")
    print(f"{'='*60}")
    
    n1 = storage.load_trie(UNIVERSAL_POOL_KEY, "n1")
    stats['pool_status']['n1_universal'] = {
        'exists': n1 is not None,
        'pattern_count': n1.pattern_count if n1 else 0,
    }
    print(f"N1 Universal: {stats['pool_status']['n1_universal']}")
    
    for ac in ["blue_chip", "large_cap", "mid_cap", "defi", "meme"]:
        n2 = storage.load_trie(class_pool_key(ac), "n2")
        key = f"n2_{ac}"
        stats['pool_status'][key] = {
            'exists': n2 is not None,
            'pattern_count': n2.pattern_count if n2 else 0,
        }
        print(f"N2 {ac}: {stats['pool_status'][key]}")
    
    return stats


if __name__ == "__main__":
    build_all_tries()
