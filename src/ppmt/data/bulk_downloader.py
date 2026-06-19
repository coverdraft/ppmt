"""Bulk Data Downloader for PPMT Trie Population.

Downloads 90 days of OHLCV data (with volume) for representative tokens
across all asset classes. This populates the shared N1/N2 pools.

Usage:
    python -m ppmt.data.bulk_downloader --exchange binance --days 90
    python -m ppmt.data.bulk_downloader --exchange binance --days 30 --symbols BTC/USDT,SOL/USDT --save-to-db
    python -m ppmt.data.bulk_downloader --exchange mexc --tokens BTC/USDT ETH/USDT
"""

import time
import argparse
from typing import Optional
import pandas as pd

# Representative tokens for each asset class
REPRESENTATIVE_TOKENS = {
    "blue_chip": ["BTC/USDT", "ETH/USDT"],
    "large_cap": ["SOL/USDT", "BNB/USDT", "XRP/USDT"],
    "mid_cap": ["LINK/USDT", "AVAX/USDT", "DOT/USDT"],
    "meme": ["DOGE/USDT", "PEPE/USDT", "WIF/USDT"],
    "defi": ["UNI/USDT", "AAVE/USDT"],
}

TIMEFRAMES = ["1m", "5m", "15m"]

# Binance kline limits
MAX_CANDLES_PER_REQUEST = 1000  # Binance limit
REQUEST_DELAY_SECONDS = 0.5  # Rate limiting


class BulkDownloader:
    """Download OHLCV data in bulk for PPMT trie population."""
    
    def __init__(self, exchange: str = "binance", api_key: str = None, 
                 api_secret: str = None):
        self.exchange = exchange
        self.api_key = api_key
        self.api_secret = api_secret
        self._exchange_obj = None
    
    def _get_exchange(self):
        """Lazy init ccxt exchange."""
        if self._exchange_obj is None:
            import ccxt
            exchange_class = getattr(ccxt, self.exchange, None)
            if exchange_class is None:
                raise ValueError(f"Exchange {self.exchange} not supported by ccxt")
            config = {'enableRateLimit': True}
            if self.api_key:
                config['apiKey'] = self.api_key
            if self.api_secret:
                config['secret'] = self.api_secret
            self._exchange_obj = exchange_class(config)
        return self._exchange_obj
    
    def download_token(self, symbol: str, timeframe: str, 
                       days: int = 90) -> pd.DataFrame:
        """Download OHLCV data for a single token/timeframe.
        
        Uses paginated requests to get all data within the date range.
        Binance limits 1000 candles per request, so we paginate backwards
        from now.
        """
        ex = self._get_exchange()
        
        # Calculate start time
        now_ms = int(time.time() * 1000)
        start_ms = now_ms - (days * 24 * 60 * 60 * 1000)
        
        all_data = []
        current_start = start_ms
        
        while current_start < now_ms:
            try:
                ohlcv = ex.fetch_ohlcv(
                    symbol, timeframe,
                    since=current_start,
                    limit=MAX_CANDLES_PER_REQUEST,
                )
                if not ohlcv:
                    break
                
                all_data.extend(ohlcv)
                
                # Next page starts after the last timestamp
                last_ts = ohlcv[-1][0]
                if last_ts <= current_start:
                    break  # No progress, stop
                current_start = last_ts + 1
                
                # Rate limiting
                time.sleep(REQUEST_DELAY_SECONDS)
                
            except Exception as e:
                print(f"  Error fetching {symbol} {timeframe}: {e}")
                time.sleep(2)  # Back off on error
                continue
        
        if not all_data:
            return pd.DataFrame()
        
        df = pd.DataFrame(all_data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = df['timestamp'].astype(int)
        df = df.drop_duplicates(subset='timestamp').sort_values('timestamp').reset_index(drop=True)
        
        return df
    
    def download_all(self, days: int = 90, 
                     tokens: dict = None,
                     timeframes: list = None,
                     storage = None) -> dict:
        """Download all tokens and store in PPMT database.
        
        Args:
            days: Number of days of history to download
            tokens: Dict of {asset_class: [symbols]}. Uses REPRESENTATIVE_TOKENS if None.
            timeframes: List of timeframes. Uses TIMEFRAMES if None.
            storage: PPMTStorage instance. If provided, saves data to DB.
        
        Returns:
            Dict with download stats.
        """
        if tokens is None:
            tokens = REPRESENTATIVE_TOKENS
        if timeframes is None:
            timeframes = TIMEFRAMES
        
        stats = {
            'total_requested': 0,
            'total_downloaded': 0,
            'total_rows': 0,
            'errors': [],
            'per_token': {},
        }
        
        # Download order matters: blue_chip first (stabilizes N1)
        ordered_classes = ["blue_chip", "large_cap", "mid_cap", "defi", "meme"]
        
        for asset_class in ordered_classes:
            symbols = tokens.get(asset_class, [])
            for symbol in symbols:
                for tf in timeframes:
                    stats['total_requested'] += 1
                    print(f"Downloading {symbol} {tf} ({asset_class})...")
                    
                    try:
                        df = self.download_token(symbol, tf, days)
                        if len(df) > 0:
                            stats['total_downloaded'] += 1
                            stats['total_rows'] += len(df)
                            
                            if storage is not None:
                                storage.save_ohlcv(symbol, tf, df)
                                print(f"  Saved {len(df)} rows to database")
                            else:
                                print(f"  Downloaded {len(df)} rows (not saved - no storage)")
                            
                            key = f"{symbol}:{tf}"
                            stats['per_token'][key] = {
                                'rows': len(df),
                                'asset_class': asset_class,
                                'timeframe': tf,
                            }
                        else:
                            stats['errors'].append(f"{symbol} {tf}: empty response")
                    except Exception as e:
                        stats['errors'].append(f"{symbol} {tf}: {str(e)}")
                    
                    time.sleep(REQUEST_DELAY_SECONDS)
        
        print(f"\n=== Download Summary ===")
        print(f"Requested: {stats['total_requested']}")
        print(f"Downloaded: {stats['total_downloaded']}")
        print(f"Total rows: {stats['total_rows']}")
        print(f"Errors: {len(stats['errors'])}")
        for err in stats['errors']:
            print(f"  - {err}")
        
        return stats


def main():
    parser = argparse.ArgumentParser(description="PPMT Bulk Data Downloader")
    parser.add_argument("--exchange", default="binance", help="Exchange name (binance, mexc, etc.)")
    parser.add_argument("--days", type=int, default=90, help="Days of history to download")
    parser.add_argument("--timeframes", nargs="+", default=TIMEFRAMES, help="Timeframes to download")
    parser.add_argument("--symbols", type=str, default=None,
                        help="Comma-separated list of symbols to download "
                             "(e.g. BTC/USDT,SOL/USDT). If omitted, downloads "
                             "the full REPRESENTATIVE_TOKENS list.")
    parser.add_argument("--save-to-db", action="store_true", help="Save to PPMT database")
    args = parser.parse_args()

    # Resolve tokens dict
    if args.symbols:
        # --symbols flag: auto-classify each symbol and build a focused dict
        from ppmt.data.classifier import AssetClassifier
        classifier = AssetClassifier()
        tokens = {}
        for sym in args.symbols.split(","):
            sym = sym.strip()
            if not sym:
                continue
            info = classifier.classify(sym)
            tokens.setdefault(info.asset_class, []).append(sym)
        print(f"--symbols mode: {args.symbols}")
        for ac, syms in tokens.items():
            print(f"  {ac}: {syms}")
    else:
        tokens = None  # uses REPRESENTATIVE_TOKENS

    storage = None
    if args.save_to_db:
        from ppmt.data.storage import PPMTStorage
        storage = PPMTStorage()
    
    downloader = BulkDownloader(exchange=args.exchange)
    downloader.download_all(days=args.days, tokens=tokens, timeframes=args.timeframes, storage=storage)


if __name__ == "__main__":
    main()
