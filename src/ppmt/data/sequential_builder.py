"""Sequential Trie Builder for PPMT Shared Pool Population.

Build order matters: BTC first (most data, stabilizes N1), then ETH,
then the rest by asset class. This ensures shared N1/N2 pools accumulate
data progressively.

Usage:
    python -m ppmt.data.sequential_builder
    python -m ppmt.data.sequential_builder --symbols BTC/USDT,SOL/USDT
    python -m ppmt.data.sequential_builder --timeframe 5m
"""

import argparse
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


def _verify_pools_in_db(storage: PPMTStorage, asset_classes_seen: set) -> None:
    """Direct SQL query to verify N1/N2 pools were actually written to DB.

    This bypasses the load_trie() method to catch serialization bugs.
    """
    import sqlite3
    conn = storage._ensure_conn()
    cursor = conn.cursor()

    # Check N1 universal
    cursor.execute(
        "SELECT length(data), updated_at FROM tries WHERE symbol = ? AND level = 'n1'",
        (UNIVERSAL_POOL_KEY,),
    )
    row = cursor.fetchone()
    if row:
        print(f"  ✓ N1 universal pool in DB: {row[0]} bytes, updated {row[1]}")
    else:
        print(f"  ✗ N1 universal pool MISSING from DB!")

    # Check N2 class pools
    for ac in asset_classes_seen:
        key = class_pool_key(ac)
        cursor.execute(
            "SELECT length(data), updated_at FROM tries WHERE symbol = ? AND level = 'n2'",
            (key,),
        )
        row = cursor.fetchone()
        if row:
            print(f"  ✓ N2 {ac} pool in DB: {row[0]} bytes, updated {row[1]}")
        else:
            print(f"  ✗ N2 {ac} pool MISSING from DB!")

    # Check N3 per-symbol pools
    cursor.execute(
        "SELECT symbol, length(data) FROM tries WHERE level = 'n3' ORDER BY symbol",
    )
    rows = cursor.fetchall()
    for sym, size in rows:
        print(f"  ✓ N3 {sym}: {size} bytes")


def build_all_tries(timeframe: str = "15m", pattern_length: int = 5, 
                    storage: PPMTStorage = None,
                    symbols: list[str] | None = None) -> dict:
    """Build tries sequentially for all tokens, populating shared pools.
    
    Args:
        timeframe: Timeframe to build
        pattern_length: Pattern length for trie construction
        storage: PPMTStorage instance (created if None)
        symbols: Optional list of specific symbols to build.
                 If provided, only these symbols are built (auto-classified).
                 If None, uses the full BUILD_ORDER.
    
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
    
    # Determine build order
    if symbols is not None:
        # Auto-classify and build in class order
        build_order = []
        classified = {}
        for sym in symbols:
            info = classifier.classify(sym)
            classified.setdefault(info.asset_class, []).append(sym)
        # Maintain class priority order
        for ac in ["blue_chip", "large_cap", "mid_cap", "defi", "meme", "new_launch"]:
            if ac in classified:
                build_order.append((ac, classified[ac]))
    else:
        build_order = BUILD_ORDER
    
    asset_classes_seen = set()
    
    for asset_class, sym_list in build_order:
        asset_classes_seen.add(asset_class)
        for symbol in sym_list:
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
                
                # Price sanity check (ENTREGABLE 13 FIX: Bug 3)
                # Detect prices that are 1M× too large (decimal point lost)
                avg_close = df["close"].mean()
                if symbol == "DOGE/USDT" and avg_close > 1.0:
                    print(f"  ⚠ PRICE BUG: DOGE avg close = {avg_close:.4f} (expected < 1.0)")
                    print(f"  Fixing: dividing all prices by 1,000,000")
                    for col in ["open", "high", "low", "close"]:
                        df[col] = df[col] / 1_000_000.0
                    # Re-save corrected data
                    storage.save_ohlcv(symbol, timeframe, df)
                    print(f"  ✓ Corrected prices saved (new avg close = {df['close'].mean():.6f})")
                
                print(f"  Data: {len(df)} candles, avg close = {df['close'].mean():.6f}")
                
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
                
                if count == 0:
                    print(f"  ⚠ build() returned 0 patterns! Check SAX encoding.")
                    stats['errors'].append(f"{symbol}: build returned 0 patterns")
                    continue
                
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
                import traceback
                msg = f"{symbol}: {str(e)}"
                print(f"  ERROR: {msg}")
                traceback.print_exc()
                stats['errors'].append(msg)
    
    # Final pool verification
    print(f"\n{'='*60}")
    print("FINAL POOL VERIFICATION")
    print(f"{'='*60}")
    
    # Method 1: Via PPMTStorage.load_trie()
    n1 = storage.load_trie(UNIVERSAL_POOL_KEY, "n1")
    stats['pool_status']['n1_universal'] = {
        'exists': n1 is not None,
        'pattern_count': n1.pattern_count if n1 else 0,
    }
    print(f"N1 Universal: {stats['pool_status']['n1_universal']}")
    
    for ac in asset_classes_seen:
        n2 = storage.load_trie(class_pool_key(ac), "n2")
        key = f"n2_{ac}"
        stats['pool_status'][key] = {
            'exists': n2 is not None,
            'pattern_count': n2.pattern_count if n2 else 0,
        }
        print(f"N2 {ac}: {stats['pool_status'][key]}")
    
    # Method 2: Direct SQL verification
    print(f"\n--- Direct SQL Verification ---")
    _verify_pools_in_db(storage, asset_classes_seen)
    
    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PPMT Sequential Trie Builder")
    parser.add_argument("--symbols", type=str, default=None,
                        help="Comma-separated list of symbols to build "
                             "(e.g. BTC/USDT,SOL/USDT). If omitted, builds all.")
    parser.add_argument("--timeframe", default="15m", help="Timeframe to build")
    parser.add_argument("--pattern-length", type=int, default=5, help="Pattern length")
    args = parser.parse_args()
    
    symbols = None
    if args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    
    build_all_tries(
        timeframe=args.timeframe,
        pattern_length=args.pattern_length,
        symbols=symbols,
    )
