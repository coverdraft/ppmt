/**
 * ╔══════════════════════════════════════════════════════════════════════════╗
 * ║  Dune Analytics Client — SQL-Powered Blockchain Data                   ║
 * ║  Decoded on-chain data via REST API on free tier                       ║
 * ╚══════════════════════════════════════════════════════════════════════════╝
 *
 * Dune Analytics (https://dune.com/):
 *   - 100+ chains supported with decoded blockchain data
 *   - Free tier: 2,500 credits/month, API access included
 *   - REST API: https://api.dune.com/api/v1/
 *   - Rate: 15 low-priority + 40 high-priority requests/min
 *   - Query execution: submit SQL → get execution_id → poll for results
 *   - Community-curated labels for wallets and contracts
 *
 * API Flow:
 *   1. POST /query/execute  — submit SQL, receive execution_id
 *   2. GET  /execution/{id}/status — poll until state is QUERY_STATE_COMPLETED
 *   3. GET  /execution/{id}/results — fetch result rows
 *
 * Environment Variable:
 *   DUNE_API_KEY — optional, free tier works without it but with lower rate limits
 */

import { RateLimiter } from './rate-limiter';
import { UnifiedCache } from './source-cache';

// ============================================================
// TYPES
// ============================================================

/** Result row from a Dune SQL query — key/value pairs keyed by column name */
export type DuneRow = Record<string, unknown>;

/** Status of a Dune query execution */
export type DuneExecutionState =
  | 'QUERY_STATE_PENDING'
  | 'QUERY_STATE_EXECUTING'
  | 'QUERY_STATE_COMPLETED'
  | 'QUERY_STATE_FAILED'
  | 'QUERY_STATE_CANCELLED'
  | 'QUERY_STATE_EXPIRED';

/** Metadata about a Dune query execution */
export interface DuneExecutionMeta {
  execution_id: string;
  query_id: number;
  state: DuneExecutionState;
  submitted_at: string;
  expires_at: string;
  execution_started_at?: string;
  execution_completed_at?: string;
  row_count?: number;
  bytes?: number;
  total_cost?: number;
}

/** Paged result set from a Dune query execution */
export interface DuneResultSet {
  rows: DuneRow[];
  metadata: DuneExecutionMeta;
  next_offset?: number;
  total_row_count?: number;
}

/** Top trader information for a token */
export interface DuneTopTrader {
  wallet: string;
  buyCount: number;
  sellCount: number;
  totalVolumeUsd: number;
  netVolumeUsd: number;
  firstTradeAt: string;
  lastTradeAt: string;
}

/** DEX swap event */
export interface DuneSwapEvent {
  txHash: string;
  blockNumber: number;
  blockTime: string;
  maker: string;
  taker: string;
  tokenIn: string;
  tokenOut: string;
  amountIn: string;
  amountOut: string;
  volumeUsd: number;
  dex: string;
}

/** Wallet label from Dune's community-curated label database */
export interface DuneWalletLabel {
  address: string;
  label: string;
  labelType: string;
  labelSubtype: string;
  projectName: string;
  contributor: string;
}

/** Token holder information */
export interface DuneTokenHolder {
  address: string;
  balance: string;
  balanceFormatted: number;
  percentageOfTotal: number;
  isContract: boolean;
}

/** Protocol TVL data point */
export interface DuneProtocolTVL {
  date: string;
  tvlUsd: number;
  tvlChange1d: number;
  tvlChange7d: number;
}

/** Utility: delay for a given number of milliseconds */
function delay(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms));
}

// ============================================================
// DUNE ANALYTICS CLIENT
// Free tier: 2,500 credits/month, API included
// ============================================================

export class DuneClient {
  private apiKey: string;
  private baseUrl = 'https://api.dune.com/api/v1';
  private limiter: RateLimiter;
  private cache: UnifiedCache;

  /** Maximum time in milliseconds to wait for a query to complete */
  private static readonly MAX_EXECUTION_WAIT_MS = 120_000;

  /** Interval in milliseconds between status polls */
  private static readonly POLL_INTERVAL_MS = 3_000;

  constructor(apiKey?: string) {
    this.apiKey = apiKey || process.env.DUNE_API_KEY || '';
    this.limiter = new RateLimiter(3, 6); // Conservative 3 RPS for free tier
    this.cache = new UnifiedCache(30); // 30 min TTL — query results are relatively stable
  }

  /**
   * Whether the client has an API key configured.
   * Dune can work without an API key for some public queries,
   * but rate limits are lower and functionality is reduced.
   */
  get isConfigured(): boolean {
    return !!this.apiKey;
  }

  // ----------------------------------------------------------
  // Internal helpers
  // ----------------------------------------------------------

  /**
   * Build headers common to all Dune API requests.
   */
  private buildHeaders(): Record<string, string> {
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      'Accept': 'application/json',
    };
    if (this.apiKey) {
      headers['X-DUNE-API-KEY'] = this.apiKey;
    }
    return headers;
  }

  /**
   * Normalise a human-readable chain name to Dune's expected format.
   * Dune uses lowercase snake_case chain identifiers in table names.
   */
  private toDuneChain(chain: string): string {
    const map: Record<string, string> = {
      'ethereum': 'ethereum',
      'eth': 'ethereum',
      'base': 'base',
      'arbitrum': 'arbitrum',
      'arb': 'arbitrum',
      'optimism': 'optimism',
      'op': 'optimism',
      'polygon': 'polygon',
      'matic': 'polygon',
      'bsc': 'bnb',
      'bnb': 'bnb',
      'binance': 'bnb',
      'avalanche': 'avalanche_c',
      'avax': 'avalanche_c',
      'fantom': 'fantom',
      'ftm': 'fantom',
      'gnosis': 'gnosis',
      'xdai': 'gnosis',
      'solana': 'solana',
      'sol': 'solana',
    };
    return map[chain.toLowerCase()] || chain.toLowerCase();
  }

  // ----------------------------------------------------------
  // Core query execution
  // ----------------------------------------------------------

  /**
   * Execute a SQL query on Dune Analytics.
   *
   * Flow: POST /query/execute → poll /execution/{id}/status → GET /execution/{id}/results
   *
   * @param sql - The SQL query string to execute against Dune's decoded tables
   * @param maxWaitMs - Maximum time to wait for query completion (default 120s)
   * @returns Array of result rows, each being a key/value record keyed by column name
   */
  async executeQuery(sql: string, maxWaitMs: number = DuneClient.MAX_EXECUTION_WAIT_MS): Promise<DuneRow[]> {
    const cacheKey = `dune:query:${sql.slice(0, 300)}`;
    const cached = this.cache.get<DuneRow[]>(cacheKey);
    if (cached) return cached;

    await this.limiter.acquire();

    try {
      // Step 1: Submit query for execution
      const executeRes = await fetch(`${this.baseUrl}/query/execute`, {
        method: 'POST',
        headers: this.buildHeaders(),
        body: JSON.stringify({ query_sql: sql }),
      });

      if (!executeRes.ok) {
        const errorBody = await executeRes.text();
        console.warn(`[Dune] Execute failed: ${executeRes.status} — ${errorBody.slice(0, 200)}`);
        return [];
      }

      const executeData = await executeRes.json() as { execution_id: string };
      const executionId = executeData.execution_id;
      if (!executionId) {
        console.warn('[Dune] No execution_id returned from /query/execute');
        return [];
      }

      // Step 2: Poll for completion
      const startTime = Date.now();
      while (Date.now() - startTime < maxWaitMs) {
        await this.limiter.acquire();

        const statusRes = await fetch(
          `${this.baseUrl}/execution/${executionId}/status`,
          { headers: this.buildHeaders() },
        );

        if (!statusRes.ok) {
          console.warn(`[Dune] Status poll failed: ${statusRes.status}`);
          await delay(DuneClient.POLL_INTERVAL_MS);
          continue;
        }

        const statusData = await statusRes.json() as { state: DuneExecutionState };
        const state = statusData.state;

        if (state === 'QUERY_STATE_COMPLETED') {
          break;
        }

        if (
          state === 'QUERY_STATE_FAILED' ||
          state === 'QUERY_STATE_CANCELLED' ||
          state === 'QUERY_STATE_EXPIRED'
        ) {
          console.warn(`[Dune] Execution ${executionId} ended with state: ${state}`);
          return [];
        }

        // Still pending / executing — wait and retry
        await delay(DuneClient.POLL_INTERVAL_MS);
      }

      // Check if we timed out
      if (Date.now() - startTime >= maxWaitMs) {
        console.warn(`[Dune] Execution ${executionId} timed out after ${maxWaitMs}ms`);
        return [];
      }

      // Step 3: Fetch results
      await this.limiter.acquire();

      const resultsRes = await fetch(
        `${this.baseUrl}/execution/${executionId}/results?limit=10000`,
        { headers: this.buildHeaders() },
      );

      if (!resultsRes.ok) {
        console.warn(`[Dune] Results fetch failed: ${resultsRes.status}`);
        return [];
      }

      const resultsData = await resultsRes.json() as DuneResultSet;
      const rows = resultsData.rows || [];

      this.cache.set(cacheKey, rows);
      console.log(`[Dune] Query returned ${rows.length} rows (execution ${executionId})`);
      return rows;
    } catch (err) {
      console.error('[Dune] Query execution error:', err);
      return [];
    }
  }

  // ----------------------------------------------------------
  // High-level typed methods
  // ----------------------------------------------------------

  /**
   * Get top traders for a token on a specific chain.
   *
   * Queries Dune's decoded DEX swap tables to aggregate per-trader
   * buy/sell counts and USD-denominated volumes over the last 30 days.
   *
   * @param tokenAddress - The ERC-20 / token contract address
   * @param chain - Blockchain identifier (e.g. 'ethereum', 'base', 'arbitrum')
   * @param limit - Maximum number of traders to return (default 100)
   */
  async getTopTraders(
    tokenAddress: string,
    chain: string = 'ethereum',
    limit: number = 100,
  ): Promise<DuneTopTrader[]> {
    const duneChain = this.toDuneChain(chain);

    const sql = `
      SELECT
        trader,
        SUM(CASE WHEN side = 'buy' THEN 1 ELSE 0 END) AS buy_count,
        SUM(CASE WHEN side = 'sell' THEN 1 ELSE 0 END) AS sell_count,
        SUM(volume_usd) AS total_volume_usd,
        SUM(CASE WHEN side = 'buy' THEN volume_usd ELSE -volume_usd END) AS net_volume_usd,
        MIN(block_time) AS first_trade_at,
        MAX(block_time) AS last_trade_at
      FROM (
        SELECT
          t."from" AS trader,
          'sell' AS side,
          t.amount_usd AS volume_usd,
          t.block_time
        FROM ${duneChain}.dex."trades" t
        WHERE t.token_sold_address = '${tokenAddress}'
          AND t.block_time > NOW() - INTERVAL '30 days'
        UNION ALL
        SELECT
          t."to" AS trader,
          'buy' AS side,
          t.amount_usd AS volume_usd,
          t.block_time
        FROM ${duneChain}.dex."trades" t
        WHERE t.token_bought_address = '${tokenAddress}'
          AND t.block_time > NOW() - INTERVAL '30 days'
      ) combined
      GROUP BY trader
      ORDER BY total_volume_usd DESC
      LIMIT ${limit}
    `;

    const rows = await this.executeQuery(sql);

    return rows.map(row => ({
      wallet: String(row.trader || ''),
      buyCount: Number(row.buy_count || 0),
      sellCount: Number(row.sell_count || 0),
      totalVolumeUsd: Number(row.total_volume_usd || 0),
      netVolumeUsd: Number(row.net_volume_usd || 0),
      firstTradeAt: String(row.first_trade_at || ''),
      lastTradeAt: String(row.last_trade_at || ''),
    }));
  }

  /**
   * Get DEX swap events for a token on a specific chain.
   *
   * Returns individual swap records from Dune's decoded DEX tables
   * for the given token within the specified number of days.
   *
   * @param tokenAddress - The token contract address
   * @param chain - Blockchain identifier
   * @param days - Number of days of history to retrieve (default 7)
   */
  async getDEXSwaps(
    tokenAddress: string,
    chain: string = 'ethereum',
    days: number = 7,
  ): Promise<DuneSwapEvent[]> {
    const duneChain = this.toDuneChain(chain);

    const sql = `
      SELECT
        tx_hash,
        block_number,
        block_time,
        "from" AS maker,
        "to" AS taker,
        token_bought_address AS token_in,
        token_sold_address AS token_out,
        token_bought_amount AS amount_in,
        token_sold_amount AS amount_out,
        amount_usd AS volume_usd,
        exchange_contract_address AS dex
      FROM ${duneChain}.dex."trades"
      WHERE (token_bought_address = '${tokenAddress}' OR token_sold_address = '${tokenAddress}')
        AND block_time > NOW() - INTERVAL '${days} days'
      ORDER BY block_time DESC
      LIMIT 5000
    `;

    const rows = await this.executeQuery(sql);

    return rows.map(row => ({
      txHash: String(row.tx_hash || ''),
      blockNumber: Number(row.block_number || 0),
      blockTime: String(row.block_time || ''),
      maker: String(row.maker || ''),
      taker: String(row.taker || ''),
      tokenIn: String(row.token_in || ''),
      tokenOut: String(row.token_out || ''),
      amountIn: String(row.amount_in || '0'),
      amountOut: String(row.amount_out || '0'),
      volumeUsd: Number(row.volume_usd || 0),
      dex: String(row.dex || ''),
    }));
  }

  /**
   * Get known labels for a wallet address from Dune's community label database.
   *
   * Dune maintains a rich, crowd-sourced label database mapping addresses
   * to entities (e.g. "Binance Hot Wallet", "Uniswap V3 Router").
   *
   * @param walletAddress - The wallet / contract address to look up
   */
  async getWalletLabels(walletAddress: string): Promise<DuneWalletLabel[]> {
    const cacheKey = `dune:labels:${walletAddress.toLowerCase()}`;
    const cached = this.cache.get<DuneWalletLabel[]>(cacheKey);
    if (cached) return cached;

    const sql = `
      SELECT
        address,
        label,
        label_type,
        label_subtype,
        project_name,
        contributor
      FROM labels."labels"
      WHERE address = '${walletAddress}'
      LIMIT 50
    `;

    const rows = await this.executeQuery(sql);

    const labels: DuneWalletLabel[] = rows.map(row => ({
      address: String(row.address || walletAddress),
      label: String(row.label || ''),
      labelType: String(row.label_type || ''),
      labelSubtype: String(row.label_subtype || ''),
      projectName: String(row.project_name || ''),
      contributor: String(row.contributor || ''),
    }));

    // Cache labels for the full 30-minute TTL since they rarely change
    this.cache.set(cacheKey, labels);
    console.log(`[Dune] Found ${labels.length} labels for ${walletAddress}`);
    return labels;
  }

  /**
   * Get top holders of a token on a specific chain.
   *
   * Queries Dune's decoded ERC-20 balance tables to return
   * the largest token holders by balance.
   *
   * @param tokenAddress - The token contract address
   * @param chain - Blockchain identifier
   * @param limit - Maximum number of holders to return (default 100)
   */
  async getTokenHolders(
    tokenAddress: string,
    chain: string = 'ethereum',
    limit: number = 100,
  ): Promise<DuneTokenHolder[]> {
    const duneChain = this.toDuneChain(chain);

    const sql = `
      SELECT
        holder_address AS address,
        raw_balance AS balance,
        raw_balance / POWER(10, decimals) AS balance_formatted,
        raw_balance / POWER(10, decimals) * 100.0 / SUM(raw_balance / POWER(10, decimals)) OVER () AS percentage_of_total,
        is_contract AS is_contract
      FROM ${duneChain}.erc20."ERC20_evt_Transfer" balances
      WHERE contract_address = '${tokenAddress}'
      ORDER BY raw_balance DESC
      LIMIT ${limit}
    `;

    const rows = await this.executeQuery(sql);

    return rows.map(row => ({
      address: String(row.address || ''),
      balance: String(row.balance || '0'),
      balanceFormatted: Number(row.balance_formatted || 0),
      percentageOfTotal: Number(row.percentage_of_total || 0),
      isContract: Boolean(row.is_contract),
    }));
  }

  /**
   * Get protocol TVL data from Dune's decoded DeFi tables.
   *
   * Queries daily TVL snapshots for a given protocol on a chain,
   * returning the most recent data first.
   *
   * @param protocolName - The protocol name as indexed by Dune (e.g. 'uniswap', 'aave')
   * @param chain - Blockchain identifier
   */
  async getProtocolTVL(
    protocolName: string,
    chain: string = 'ethereum',
  ): Promise<DuneProtocolTVL[]> {
    const duneChain = this.toDuneChain(chain);

    const sql = `
      SELECT
        date_day AS date,
        tvl_usd,
        tvl_usd - LAG(tvl_usd) OVER (ORDER BY date_day) AS tvl_change_1d,
        tvl_usd - LAG(tvl_usd, 7) OVER (ORDER BY date_day) AS tvl_change_7d
      FROM ${duneChain}.defi."protocol_tvl"
      WHERE protocol_name = '${protocolName}'
      ORDER BY date_day DESC
      LIMIT 90
    `;

    const rows = await this.executeQuery(sql);

    return rows.map(row => ({
      date: String(row.date || ''),
      tvlUsd: Number(row.tvl_usd || 0),
      tvlChange1d: Number(row.tvl_change_1d || 0),
      tvlChange7d: Number(row.tvl_change_7d || 0),
    }));
  }
}

// ============================================================
// SINGLETON EXPORT
// ============================================================

export const duneClient = new DuneClient();
