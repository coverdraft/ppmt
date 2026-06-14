# Task: PPMT Validation Features (v0.7.0)

## Summary
Implemented out-of-sample validation and walk-forward analysis features for the PPMT (Pattern Prediction Market Trader) CLI tool.

## Files Modified

### 1. `/home/z/my-project/ppmt/src/ppmt/__init__.py`
- Bumped version from `0.6.1` to `0.7.0`

### 2. `/home/z/my-project/ppmt/pyproject.toml`
- Bumped version from `0.6.3` to `0.7.0`

### 3. `/home/z/my-project/ppmt/src/ppmt/cli/main.py`
Major changes:

#### Task 1a: `--train-ratio` option on `ppmt build`
- Added `--train-ratio` option (default 1.0) to the `build` command
- When `train_ratio < 1.0`:
  - Splits DataFrame into `df_train` (first `train_ratio`%) and `df_test` (rest)
  - Builds trie on training data only (`df_build = df_train`)
  - Runs bootstrap on training data only
  - Computes PAA normalization stats (`paa_mean`, `paa_std`) from training SAX encoding
  - Stores training stats in engine state via `storage.save_engine_state()`
  - Displays training normalization stats at end of build

#### Task 1b: Training stats in engine state
- When `train_ratio < 1.0`, adds to engine state:
  - `train_ratio`: the split ratio
  - `paa_mean`: PAA normalization mean from training
  - `paa_std`: PAA normalization std from training
  - `train_candle_count`: number of training candles
  - `total_candle_count`: total candles in dataset
- The `stats` command now displays these values when present

#### Task 1c: `ppmt validate` command
New CLI command automating the entire OOS workflow:
- **Options**: `--symbol`, `--timeframe`, `--train-ratio` (default 0.7), `--capital`, `--min-confidence`, `--simulations` (MC), `--seed`
- **Workflow**:
  1. Loads all data, splits into train/test at DataFrame level
  2. Encodes training data, gets PAA normalization stats
  3. Builds trie on training data ONLY (with pre-computed symbols)
  4. Runs bootstrap on training data (1 pass)
  5. Saves trie to storage
  6. Paper trades on test data with training stats, `living_trie=False`
  7. Runs Monte Carlo on OOS trades
  8. Displays comprehensive IS vs OOS comparison report
  9. Restores original trie after completion
- **Report format**: Rich-formatted with degradation analysis and verdict

#### Task 2: `ppmt walk-forward` command
New CLI command for walk-forward analysis:
- **Options**: `--symbol`, `--timeframe`, `--folds` (default 5), `--min-confidence`, `--capital`
- **Logic**: Expanding window across `(folds + 1)` equal segments
  - Fold 0: Train on segment 0, Test on segment 1
  - Fold 1: Train on segments 0-1, Test on segment 2
  - etc.
- Each fold:
  1. Builds fresh trie on training portion
  2. Runs bootstrap on training portion (1 pass)
  3. Computes training PAA stats
  4. Paper trades on test portion (`living_trie=False`, training stats)
  5. Fresh capital ($10,000) per fold
- **Output**: Per-fold results + aggregate summary with verdict
- Restores original trie after completion

## Key Design Decisions
1. **Original trie preservation**: Both `validate` and `walk-forward` save the original trie and restore it after completion, so the user's existing Living Trie is not destroyed
2. **Pre-computed symbols**: When building the trie on training data, we use `encode_with_normalization()` to get pre-computed symbols, then pass them to `engine.build(df_train, symbols=train_symbols)`. This ensures the trie is built with consistent SAX symbols
3. **Edge cases**: Handled insufficient data, no trades, empty datasets gracefully
4. **No modifications** to `paper_trader.py`, `ppmt.py`, or core SAX/trie code as specified

## Testing
- Syntax validation: ✅
- Command registration: ✅ (all 10 commands present)
- Version check: ✅ (0.7.0)
- Build --train-ratio option: ✅
- Validate --help: ✅
- Walk-forward --help: ✅
- Invalid train_ratio handling: ✅
- Integration test with synthetic data: ✅
- Engine state round-trip with train stats: ✅
- Graceful error handling with no data: ✅
