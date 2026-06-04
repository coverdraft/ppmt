/**
 * On-Chain Data Ingestion Pipeline - CryptoQuant Terminal
 * 
 * Connects to real blockchain data sources:
 * - CoinGecko API (PRIMARY - market data, prices, volumes, OHLCV) [FREE, no API key]
 * - Solana RPC (mainnet-beta)
 * - Ethereum RPC
 * - DexScreener API (multi-chain token data, DEX pairs/pools)
 * - Jupiter API (Solana swap aggregator)
 * - DexPaprika API (35 chains, pool swaps, buy/sell ratios)
 * - Helius API (Solana enhanced transactions)
 * - Etherscan API (ETH transaction history)
 * 
 * Data source priority:
 *   1. CoinGecko (market data: prices, volumes, market caps, OHLCV)
 *   2. DexScreener (DEX-specific: pairs, pools, buy/sell ratios)
 *   3. DexPaprika (35-chain coverage, pool-level data)
 * 
 * Production should use paid RPCs for reliability.
 */

// ============================================================
// TYPES
// ============================================================

export interface IngestionConfig {
  solanaRpcUrl: string;
  ethereumRpcUrl: string;
  dexscreenerApiUrl: string;
  jupiterApiUrl: string;
  heliusApiKey?: string;
  etherscanApiKey?: string;
  coingeckoApiUrl: string;
}

export interface OnChainTransaction {
  txHash: string;
  blockNumber: number;
  blockTime: Date;
  chain: string;
  from: string;
  to: string;
  value: string;
  gasUsed?: number;
  gasPrice?: string;
  methodId?: string;
  logs?: TokenTransferLog[];
}

export interface TokenTransferLog {
  from: string;
  to: string;
  tokenAddress: string;
  amount: string;
  decimals: number;
}

export interface DexScreenerToken {
  chainId: string;
  dexId: string;
  pairAddress: string;
  baseToken: { address: string; symbol: string; name: string };
  quoteToken: { address: string; symbol: string; name: string };
  priceNative: string;
  priceUsd: string;
  priceChange?: { h24: number; h6: number; h1: number; m5: number };
  txns: { h24: { buys: number; sells: number }; h6: { buys: number; sells: number }; h1: { buys: number; sells: number } };
  volume: { h24: number; h6: number; h1: number };
  liquidity: { usd: number; base: number; quote: number };
  fdv: number;
  marketCap: number;
  pairCreatedAt: number;
  info?: { imageUrl: string; websites: { url: string }[]; socials: { type: string; url: string }[] };
}

export interface JupiterSwapEvent {
  swapEvent: {
    inputMint: string;
    inputAmount: number;
    outputMint: string;
    outputAmount: number;
    fee: number;
    feeMint: string;
  };
  signer: string;
  signature: string;
  slot: number;
  timestamp: number;
}

export interface WalletTransactionHistory {
  address: string;
  chain: string;
  transactions: ParsedTransaction[];
  totalCount: number;
  hasMore: boolean;
}

export interface ParsedTransaction {
  txHash: string;
  blockTime: Date;
  action: 'BUY' | 'SELL' | 'TRANSFER' | 'ADD_LIQUIDITY' | 'REMOVE_LIQUIDITY' | 'SWAP' | 'UNKNOWN';
  tokenAddress: string;
  tokenSymbol?: string;
  quoteToken?: string;
  amountIn: number;
  amountOut: number;
  valueUsd: number;
  dex?: string;
  slippageBps?: number;
  isFrontrun: boolean;
  isSandwich: boolean;
  priorityFee?: number;
  gasUsed?: number;
}

// ============================================================
// DEFAULT CONFIG
// ============================================================

export const DEFAULT_CONFIG: IngestionConfig = {
  solanaRpcUrl: process.env.SOLANA_RPC_URL || 'https://api.mainnet-beta.solana.com',
  ethereumRpcUrl: process.env.ETHEREUM_RPC_URL || 'https://eth.llamarpc.com',
  dexscreenerApiUrl: 'https://api.dexscreener.com',
  jupiterApiUrl: 'https://quote-api.jup.ag/v6',
  heliusApiKey: process.env.HELIUS_API_KEY,
  etherscanApiKey: process.env.ETHERSCAN_API_KEY,
  coingeckoApiUrl: 'https://api.coingecko.com/api/v3',
};

// ============================================================
// DEXSCREENER CLIENT - Multi-chain token data
// ============================================================

export class DexScreenerClient {
  private baseUrl: string;
  
  constructor(baseUrl = DEFAULT_CONFIG.dexscreenerApiUrl) {
    this.baseUrl = baseUrl;
  }
  
  /**
   * Search for tokens by name/symbol
   */
  async searchTokens(query: string): Promise<DexScreenerToken[]> {
    try {
      const res = await fetch(`${this.baseUrl}/latest/dex/search?q=${encodeURIComponent(query)}`);
      if (!res.ok) throw new Error(`DexScreener search failed: ${res.status}`);
      const data = await res.json();
      return data.pairs || [];
    } catch (error) {
      console.error('DexScreener search error:', error);
      return [];
    }
  }
  
  /**
   * Get token data by chain and address
   */
  async getTokenByAddress(chainId: string, address: string): Promise<DexScreenerToken | null> {
    try {
      const res = await fetch(`${this.baseUrl}/latest/dex/tokens/${address}`);
      if (!res.ok) throw new Error(`DexScreener token lookup failed: ${res.status}`);
      const data = await res.json();
      const pairs: DexScreenerToken[] = data.pairs || [];
      // Return the pair matching the requested chain
      return pairs.find(p => p.chainId === chainId) || pairs[0] || null;
    } catch (error) {
      console.error('DexScreener token lookup error:', error);
      return null;
    }
  }
  
  /**
   * Get trending tokens across all chains
   */
  async getTrendingTokens(): Promise<DexScreenerToken[]> {
    try {
      const res = await fetch(`${this.baseUrl}/latest/dex/search?q=trending`);
      if (!res.ok) throw new Error(`DexScreener trending failed: ${res.status}`);
      const data = await res.json();
      return data.pairs || [];
    } catch (error) {
      console.error('DexScreener trending error:', error);
      return [];
    }
  }
  
  /**
   * Get token pairs by DEX
   */
  async getPairsByDex(dexId: string): Promise<DexScreenerToken[]> {
    try {
      const res = await fetch(`${this.baseUrl}/latest/dex/pairs/${dexId}`);
      if (!res.ok) throw new Error(`DexScreener pairs failed: ${res.status}`);
      const data = await res.json();
      return data.pairs || [];
    } catch (error) {
      console.error('DexScreener pairs error:', error);
      return [];
    }
  }
  
  /**
   * Get boosted tokens (token profiles that paid for visibility)
   * These are often new tokens with marketing budgets
   */
  async getBoostedTokens(): Promise<DexScreenerToken[]> {
    try {
      const res = await fetch(`${this.baseUrl}/latest/dex/search?q=boosted`);
      if (!res.ok) throw new Error(`DexScreener boosted failed: ${res.status}`);
      const data = await res.json();
      return data.pairs || [];
    } catch (error) {
      console.error('DexScreener boosted error:', error);
      return [];
    }
  }
}

// Use CoinGecko for price/market data
// and Etherscan for wallet transactions.

// ============================================================
// JUPITER CLIENT - Solana swap aggregator
// ============================================================

export class JupiterClient {
  private baseUrl: string;
  
  constructor(baseUrl = DEFAULT_CONFIG.jupiterApiUrl) {
    this.baseUrl = baseUrl;
  }
  
  /**
   * Get swap quote
   */
  async getQuote(
    inputMint: string,
    outputMint: string,
    amount: number,
    slippageBps = 50
  ) {
    try {
      const res = await fetch(
        `${this.baseUrl}/quote?inputMint=${inputMint}&outputMint=${outputMint}&amount=${amount}&slippageBps=${slippageBps}`
      );
      if (!res.ok) throw new Error(`Jupiter quote failed: ${res.status}`);
      return await res.json();
    } catch (error) {
      console.error('Jupiter quote error:', error);
      return null;
    }
  }
  
  /**
   * Get token list from Jupiter
   */
  async getTokenList() {
    try {
      const res = await fetch('https://token.jup.ag/strict');
      if (!res.ok) throw new Error(`Jupiter token list failed: ${res.status}`);
      return await res.json();
    } catch (error) {
      console.error('Jupiter token list error:', error);
      return [];
    }
  }
}

// ============================================================
// SOLANA RPC CLIENT - Direct on-chain data
// ============================================================

export class SolanaRpcClient {
  private rpcUrl: string;
  
  constructor(rpcUrl = DEFAULT_CONFIG.solanaRpcUrl) {
    this.rpcUrl = rpcUrl;
  }
  
  private async rpcCall(method: string, params: unknown[] = []) {
    try {
      const res = await fetch(this.rpcUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          jsonrpc: '2.0',
          id: Date.now(),
          method,
          params,
        }),
      });
      if (!res.ok) throw new Error(`Solana RPC ${method} failed: ${res.status}`);
      const data = await res.json();
      return data.result;
    } catch (error) {
      console.error(`Solana RPC ${method} error:`, error);
      return null;
    }
  }
  
  /**
   * Get account info for a wallet
   */
  async getAccountInfo(address: string) {
    return this.rpcCall('getAccountInfo', [address, { encoding: 'jsonParsed' }]);
  }
  
  /**
   * Get transaction signatures for a wallet
   */
  async getSignaturesForAddress(address: string, limit = 100) {
    return this.rpcCall('getSignaturesForAddress', [address, { limit }]);
  }
  
  /**
   * Get parsed transaction details
   */
  async getTransaction(signature: string) {
    return this.rpcCall('getTransaction', [
      signature,
      { maxSupportedTransactionVersion: 0, encoding: 'jsonParsed' },
    ]);
  }
  
  /**
   * Get current slot (block height)
   */
  async getSlot() {
    return this.rpcCall('getSlot');
  }
  
  /**
   * Subscribe to account changes (WebSocket)
   */
  getWsUrl(): string {
    return this.rpcUrl.replace('https://', 'wss://').replace('http://', 'ws://');
  }
}

// ============================================================
// ETHEREUM RPC CLIENT - Direct on-chain data
// ============================================================

export class EthereumRpcClient {
  private rpcUrl: string;
  
  constructor(rpcUrl = DEFAULT_CONFIG.ethereumRpcUrl) {
    this.rpcUrl = rpcUrl;
  }
  
  private async rpcCall(method: string, params: unknown[] = []) {
    try {
      const res = await fetch(this.rpcUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          jsonrpc: '2.0',
          id: Date.now(),
          method,
          params,
        }),
      });
      if (!res.ok) throw new Error(`ETH RPC ${method} failed: ${res.status}`);
      const data = await res.json();
      return data.result;
    } catch (error) {
      console.error(`ETH RPC ${method} error:`, error);
      return null;
    }
  }
  
  /**
   * Get transaction count for a wallet (nonce)
   */
  async getTransactionCount(address: string): Promise<number> {
    const result = await this.rpcCall('eth_getTransactionCount', [address, 'latest']);
    return result ? parseInt(result, 16) : 0;
  }
  
  /**
   * Get transaction by hash
   */
  async getTransactionByHash(txHash: string) {
    return this.rpcCall('eth_getTransactionByHash', [txHash]);
  }
  
  /**
   * Get transaction receipt
   */
  async getTransactionReceipt(txHash: string) {
    return this.rpcCall('eth_getTransactionReceipt', [txHash]);
  }
  
  /**
   * Get current block number
   */
  async getBlockNumber(): Promise<number> {
    const result = await this.rpcCall('eth_blockNumber');
    return result ? parseInt(result, 16) : 0;
  }
  
  /**
   * Get logs for a contract (transfer events)
   */
  async getLogs(
    address: string,
    fromBlock: string,
    toBlock: string,
    topics: string[] = []
  ) {
    return this.rpcCall('eth_getLogs', [{
      address,
      fromBlock,
      toBlock,
      topics,
    }]);
  }
}

// ============================================================
// MAIN PIPELINE ORCHESTRATOR
// ============================================================

export class DataIngestionPipeline {
  private dexscreener: DexScreenerClient;
  private jupiter: JupiterClient;
  private solana: SolanaRpcClient;
  private ethereum: EthereumRpcClient;
  private dexpaprika: import('./dexpaprika-client').DexPaprikaClient;
  private coingecko: import('./coingecko-client').CoinGeckoClient;
  
  constructor(config: Partial<IngestionConfig> = {}) {
    const merged = { ...DEFAULT_CONFIG, ...config };
    this.dexscreener = new DexScreenerClient(merged.dexscreenerApiUrl);
    this.jupiter = new JupiterClient(merged.jupiterApiUrl);
    this.solana = new SolanaRpcClient(merged.solanaRpcUrl);
    this.ethereum = new EthereumRpcClient(merged.ethereumRpcUrl);
    // DexPaprika - 35 chains, free, no API key needed
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const { DexPaprikaClient } = require('./dexpaprika-client');
    this.dexpaprika = new DexPaprikaClient();
    // CoinGecko - PRIMARY free data source for market data
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const { CoinGeckoClient } = require('./coingecko-client');
    this.coingecko = new CoinGeckoClient(merged.coingeckoApiUrl);
  }
  
  /**
   * Sync token data from CoinGecko (PRIMARY) + DexScreener + DexPaprika
   *
   * Priority:
   *   1. CoinGecko - market data (prices, volumes, market caps) [FREE, no key]
   *   2. DexScreener - DEX-specific data (pairs, pools, buy/sell) [FREE]
   *   3. DexPaprika - 35-chain pool data [FREE]
   */
  async syncTokenData(chainId = 'solana') {
    // CoinGecko as PRIMARY source for market data
    let coinGeckoTokens: import('./coingecko-client').CoinGeckoMappedToken[] = [];
    try {
      coinGeckoTokens = await this.coingecko.getTopTokens(50);
    } catch (error) {
      console.warn('[DataIngestion] CoinGecko getTopTokens failed:', error);
    }

    // DexScreener for DEX-specific pair/pool data
    const dexTokensPromise = this.dexscreener.getTrendingTokens();

    // DexPaprika for 35-chain pool data with buy/sell ratios
    const dpPoolsPromise = this.dexpaprika.getPools(chainId, 50).catch(() => ({ pools: [], cursor: undefined }));

    // CoinGecko trending as supplementary source
    const cgTrendingPromise = this.coingecko.getTrending().catch(() => []);

    const [dexTokens, cgTrending, dpPools] = await Promise.all([
      dexTokensPromise,
      cgTrendingPromise,
      dpPoolsPromise,
    ]);

    // Convert CoinGecko tokens to DexScreener-compatible format for unified handling
    const coinGeckoAsDexTokens = coinGeckoTokens.map(t =>
      this.coingecko.toDexScreenerToken(t, chainId)
    );

    return {
      // CoinGecko tokens (primary market data)
      coinGeckoTokens,
      coinGeckoAsDexTokens,
      // DexScreener tokens (DEX-specific data)
      dexTokens: dexTokens.filter(t => t.chainId === chainId),
      // CoinGecko trending tokens
      cgTrending,
      // DexPaprika pools
      dexpaprikaPools: dpPools.pools,
      // Counts
      totalCoinGeckoTokens: coinGeckoTokens.length,
      totalDexTokens: dexTokens.length,
      totalCoinGeckoTrending: cgTrending.length,
      totalDexPaprikaPools: dpPools.pools.length,
    };
  }
  
  /**
   * Get wallet transaction history across chains
   */
  async getWalletHistory(
    address: string,
    chain = 'SOL'
  ): Promise<WalletTransactionHistory> {
    if (chain === 'SOL' || chain === 'SOLANA') {
      const signatures = await this.solana.getSignaturesForAddress(address);
      const transactions: ParsedTransaction[] = [];
      
      if (signatures && Array.isArray(signatures)) {
        // Process each signature (rate limit to avoid RPC limits)
        for (const sig of signatures.slice(0, 20)) {
          const tx = await this.solana.getTransaction(sig.signature);
          if (tx) {
            transactions.push(this.parseSolanaTransaction(tx, address));
          }
        }
      }
      
      // For wallet data, use Solana RPC or Helius API
      
      return {
        address,
        chain: 'SOL',
        transactions: transactions.slice(0, 100),
        totalCount: signatures?.length || 0,
        hasMore: (signatures?.length || 0) > 20,
      };
    }
    
    if (chain === 'ETH' || chain === 'ETHEREUM') {
      const txCount = await this.ethereum.getTransactionCount(address);
      return {
        address,
        chain: 'ETH',
        transactions: [], // Need Etherscan API for full history
        totalCount: txCount,
        hasMore: txCount > 0,
      };
    }
    
    return {
      address,
      chain,
      transactions: [],
      totalCount: 0,
      hasMore: false,
    };
  }
  
  /**
   * Parse a Solana transaction into our standard format
   */
  private parseSolanaTransaction(tx: Record<string, unknown>, walletAddress: string): ParsedTransaction {
    const meta = tx.meta as Record<string, unknown> | null;
    const message = (tx.transaction as Record<string, unknown>)?.message as Record<string, unknown> | null;
    
    // Try to extract swap data from inner instructions
    let action: ParsedTransaction['action'] = 'UNKNOWN';
    let tokenAddress = '';
    let amountIn = 0;
    let amountOut = 0;
    let valueUsd = 0;
    let dex: string | undefined;
    
    if (meta?.innerInstructions) {
      action = 'SWAP';
      // Simplified parsing - production needs full instruction parsing
    }
    
    if (message?.instructions && Array.isArray(message.instructions)) {
      for (const ix of message.instructions as Record<string, unknown>[]) {
        const programId = ix.programId as string || ix.program as string;
        if (programId?.includes('jupiter')) dex = 'jupiter';
        else if (programId?.includes('raydium')) dex = 'raydium';
        else if (programId?.includes('orca')) dex = 'orca';
        else if (programId?.includes('meteora')) dex = 'meteora';
      }
    }
    
    const slot = tx.slot as number || 0;
    
    return {
      txHash: (tx.transaction as Record<string, unknown>)?.signatures?.[0] as string || '',
      blockTime: new Date((tx.blockTime as number) || Date.now()),
      action,
      tokenAddress,
      amountIn,
      amountOut,
      valueUsd,
      dex,
      isFrontrun: false,
      isSandwich: false,
      priorityFee: (meta?.prioritizationFee as number) || 0,
    };
  }
  
  /**
   * Search for tokens across all sources.
   * CoinGecko first (comprehensive), then DexScreener for DEX data.
   */
  async searchTokens(query: string) {
    // Try CoinGecko first for comprehensive market data search
    let coinGeckoResults: import('./coingecko-client').CoinGeckoSearchResult['coins'] = [];
    try {
      coinGeckoResults = await this.coingecko.searchTokens(query);
    } catch (error) {
      console.warn('[DataIngestion] CoinGecko search failed, using DexScreener only:', error);
    }

    // DexScreener for DEX-specific pair data
    const [dexResults] = await Promise.all([
      this.dexscreener.searchTokens(query),
    ]);

    return {
      coinGeckoResults,
      results: dexResults,
      total: dexResults.length + coinGeckoResults.length,
    };
  }
  
  /**
   * Get new token listings
   */
  async getNewListings(chain = 'solana') {
    // Use CoinGecko trending instead
    try {
      const trending = await this.coingecko.getTrending();
      return trending.map(t => ({
        address: t.item?.id || '',
        symbol: t.item?.symbol || '',
        name: t.item?.name || '',
        price: 0,
        priceChange24h: 0,
        volume24h: 0,
        marketCap: 0,
        liquidity: 0,
      }));
    } catch {
      return [];
    }
  }
  
  /**
   * Cross-chain token search via DexPaprika (35 chains)
   */
  async crossChainSearch(tokenAddress: string) {
    return this.dexpaprika.crossChainSearch(tokenAddress);
  }

  /**
   * Get pool swaps with wallet addresses via DexPaprika
   */
  async getPoolSwaps(chain: string, poolId: string, limit = 50) {
    return this.dexpaprika.getPoolSwaps(chain, poolId, limit);
  }

  /**
   * Get buy/sell pressure via DexPaprika
   */
  async getBuySellPressure(chain: string, poolId: string) {
    return this.dexpaprika.getBuySellPressure(chain, poolId);
  }

  // Getters for individual clients
  getDexScreener() { return this.dexscreener; }
  getJupiter() { return this.jupiter; }
  getSolana() { return this.solana; }
  getEthereum() { return this.ethereum; }
  getDexPaprika() { return this.dexpaprika; }
  getCoinGecko() { return this.coingecko; }
}
