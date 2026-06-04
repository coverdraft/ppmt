-- CreateTable
CREATE TABLE "Token" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "symbol" TEXT NOT NULL,
    "name" TEXT NOT NULL,
    "address" TEXT NOT NULL,
    "chain" TEXT NOT NULL DEFAULT 'SOL',
    "priceUsd" REAL NOT NULL DEFAULT 0,
    "volume24h" REAL NOT NULL DEFAULT 0,
    "liquidity" REAL NOT NULL DEFAULT 0,
    "marketCap" REAL NOT NULL DEFAULT 0,
    "priceChange5m" REAL NOT NULL DEFAULT 0,
    "priceChange15m" REAL NOT NULL DEFAULT 0,
    "priceChange1h" REAL NOT NULL DEFAULT 0,
    "priceChange6h" REAL NOT NULL DEFAULT 0,
    "priceChange24h" REAL NOT NULL DEFAULT 0,
    "dexId" TEXT,
    "pairAddress" TEXT,
    "dex" TEXT,
    "pairUrl" TEXT,
    "holderCount" INTEGER NOT NULL DEFAULT 0,
    "uniqueWallets24h" INTEGER NOT NULL DEFAULT 0,
    "botActivityPct" REAL NOT NULL DEFAULT 0,
    "smartMoneyPct" REAL NOT NULL DEFAULT 0,
    "createdAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" DATETIME NOT NULL
);

-- CreateTable
CREATE TABLE "Trader" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "address" TEXT NOT NULL,
    "chain" TEXT NOT NULL DEFAULT 'SOL',
    "ensName" TEXT,
    "solName" TEXT,
    "primaryLabel" TEXT NOT NULL DEFAULT 'UNKNOWN',
    "subLabels" TEXT NOT NULL DEFAULT '[]',
    "labelConfidence" REAL NOT NULL DEFAULT 0,
    "isBot" BOOLEAN NOT NULL DEFAULT false,
    "botType" TEXT,
    "botConfidence" REAL NOT NULL DEFAULT 0,
    "botDetectionSignals" TEXT NOT NULL DEFAULT '[]',
    "botDetectionVersion" TEXT NOT NULL DEFAULT '1.0',
    "botFirstDetectedAt" DATETIME,
    "totalTrades" INTEGER NOT NULL DEFAULT 0,
    "winRate" REAL NOT NULL DEFAULT 0,
    "avgPnl" REAL NOT NULL DEFAULT 0,
    "totalPnl" REAL NOT NULL DEFAULT 0,
    "avgHoldTimeMin" REAL NOT NULL DEFAULT 0,
    "avgTradeSizeUsd" REAL NOT NULL DEFAULT 0,
    "largestTradeUsd" REAL NOT NULL DEFAULT 0,
    "totalVolumeUsd" REAL NOT NULL DEFAULT 0,
    "maxDrawdown" REAL NOT NULL DEFAULT 0,
    "sharpeRatio" REAL NOT NULL DEFAULT 0,
    "profitFactor" REAL NOT NULL DEFAULT 0,
    "avgSlippageBps" INTEGER NOT NULL DEFAULT 0,
    "frontrunCount" INTEGER NOT NULL DEFAULT 0,
    "frontrunByCount" INTEGER NOT NULL DEFAULT 0,
    "sandwichCount" INTEGER NOT NULL DEFAULT 0,
    "sandwichVictimCount" INTEGER NOT NULL DEFAULT 0,
    "washTradeScore" REAL NOT NULL DEFAULT 0,
    "copyTradeScore" REAL NOT NULL DEFAULT 0,
    "mevExtractionUsd" REAL NOT NULL DEFAULT 0,
    "avgTimeBetweenTrades" REAL NOT NULL DEFAULT 0,
    "tradingHourPattern" TEXT NOT NULL DEFAULT '[]',
    "tradingDayPattern" TEXT NOT NULL DEFAULT '[]',
    "isActiveAtNight" BOOLEAN NOT NULL DEFAULT false,
    "isActive247" BOOLEAN NOT NULL DEFAULT false,
    "consistencyScore" REAL NOT NULL DEFAULT 0,
    "uniqueTokensTraded" INTEGER NOT NULL DEFAULT 0,
    "avgPositionsAtOnce" INTEGER NOT NULL DEFAULT 0,
    "maxPositionsAtOnce" INTEGER NOT NULL DEFAULT 0,
    "preferredChains" TEXT NOT NULL DEFAULT '[]',
    "preferredDexes" TEXT NOT NULL DEFAULT '[]',
    "preferredTokenTypes" TEXT NOT NULL DEFAULT '[]',
    "isSmartMoney" BOOLEAN NOT NULL DEFAULT false,
    "smartMoneyScore" REAL NOT NULL DEFAULT 0,
    "earlyEntryCount" INTEGER NOT NULL DEFAULT 0,
    "avgEntryRank" REAL NOT NULL DEFAULT 0,
    "avgExitMultiplier" REAL NOT NULL DEFAULT 0,
    "topCallCount" INTEGER NOT NULL DEFAULT 0,
    "worstCallCount" INTEGER NOT NULL DEFAULT 0,
    "isWhale" BOOLEAN NOT NULL DEFAULT false,
    "whaleScore" REAL NOT NULL DEFAULT 0,
    "totalHoldingsUsd" REAL NOT NULL DEFAULT 0,
    "avgPositionUsd" REAL NOT NULL DEFAULT 0,
    "priceImpactAvg" REAL NOT NULL DEFAULT 0,
    "isSniper" BOOLEAN NOT NULL DEFAULT false,
    "sniperScore" REAL NOT NULL DEFAULT 0,
    "avgBlockToTrade" REAL NOT NULL DEFAULT 0,
    "block0EntryCount" INTEGER NOT NULL DEFAULT 0,
    "firstSeen" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "lastActive" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "lastAnalyzed" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "analysisVersion" INTEGER NOT NULL DEFAULT 1,
    "dataQuality" REAL NOT NULL DEFAULT 0,
    "createdAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" DATETIME NOT NULL
);

-- CreateTable
CREATE TABLE "TraderTransaction" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "traderId" TEXT NOT NULL,
    "txHash" TEXT NOT NULL,
    "blockNumber" INTEGER,
    "blockTime" DATETIME NOT NULL,
    "chain" TEXT NOT NULL DEFAULT 'SOL',
    "dex" TEXT,
    "action" TEXT NOT NULL,
    "tokenAddress" TEXT NOT NULL,
    "tokenSymbol" TEXT,
    "quoteToken" TEXT,
    "amountIn" REAL NOT NULL DEFAULT 0,
    "amountOut" REAL NOT NULL DEFAULT 0,
    "priceUsd" REAL NOT NULL DEFAULT 0,
    "valueUsd" REAL NOT NULL DEFAULT 0,
    "slippageBps" INTEGER,
    "pnlUsd" REAL,
    "isFrontrun" BOOLEAN NOT NULL DEFAULT false,
    "isSandwich" BOOLEAN NOT NULL DEFAULT false,
    "isWashTrade" BOOLEAN NOT NULL DEFAULT false,
    "isJustInTime" BOOLEAN NOT NULL DEFAULT false,
    "pairedTxHash" TEXT,
    "gasUsed" REAL,
    "gasPrice" REAL,
    "priorityFee" REAL,
    "totalFeeUsd" REAL,
    "tokenAgeAtTrade" REAL,
    "holderCountAtTrade" INTEGER,
    "liquidityAtTrade" REAL,
    "logIndex" INTEGER,
    "metadata" TEXT NOT NULL DEFAULT '{}',
    "createdAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "TraderTransaction_traderId_fkey" FOREIGN KEY ("traderId") REFERENCES "Trader" ("id") ON DELETE RESTRICT ON UPDATE CASCADE
);

-- CreateTable
CREATE TABLE "WalletTokenHolding" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "traderId" TEXT NOT NULL,
    "tokenAddress" TEXT NOT NULL,
    "tokenSymbol" TEXT,
    "chain" TEXT NOT NULL DEFAULT 'SOL',
    "balance" REAL NOT NULL DEFAULT 0,
    "valueUsd" REAL NOT NULL DEFAULT 0,
    "avgEntryPrice" REAL NOT NULL DEFAULT 0,
    "unrealizedPnl" REAL NOT NULL DEFAULT 0,
    "unrealizedPnlPct" REAL NOT NULL DEFAULT 0,
    "firstBuyAt" DATETIME,
    "lastTradeAt" DATETIME,
    "buyCount" INTEGER NOT NULL DEFAULT 0,
    "sellCount" INTEGER NOT NULL DEFAULT 0,
    "totalBoughtUsd" REAL NOT NULL DEFAULT 0,
    "totalSoldUsd" REAL NOT NULL DEFAULT 0,
    "updatedAt" DATETIME NOT NULL,
    CONSTRAINT "WalletTokenHolding_traderId_fkey" FOREIGN KEY ("traderId") REFERENCES "Trader" ("id") ON DELETE RESTRICT ON UPDATE CASCADE
);

-- CreateTable
CREATE TABLE "TraderBehaviorPattern" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "traderId" TEXT NOT NULL,
    "pattern" TEXT NOT NULL,
    "confidence" REAL NOT NULL DEFAULT 0,
    "dataPoints" INTEGER NOT NULL DEFAULT 0,
    "firstObserved" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "lastObserved" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "metadata" TEXT NOT NULL DEFAULT '{}',
    CONSTRAINT "TraderBehaviorPattern_traderId_fkey" FOREIGN KEY ("traderId") REFERENCES "Trader" ("id") ON DELETE RESTRICT ON UPDATE CASCADE
);

-- CreateTable
CREATE TABLE "CrossChainWallet" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "primaryWalletId" TEXT NOT NULL,
    "linkedWalletId" TEXT NOT NULL,
    "primaryChain" TEXT NOT NULL,
    "linkedChain" TEXT NOT NULL,
    "linkedAddress" TEXT NOT NULL,
    "linkType" TEXT NOT NULL DEFAULT 'SAME_ENTITY',
    "linkConfidence" REAL NOT NULL DEFAULT 0,
    "evidence" TEXT NOT NULL DEFAULT '[]',
    "bridgeTxCount" INTEGER NOT NULL DEFAULT 0,
    "totalBridgedUsd" REAL NOT NULL DEFAULT 0,
    "createdAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "CrossChainWallet_primaryWalletId_fkey" FOREIGN KEY ("primaryWalletId") REFERENCES "Trader" ("id") ON DELETE RESTRICT ON UPDATE CASCADE,
    CONSTRAINT "CrossChainWallet_linkedWalletId_fkey" FOREIGN KEY ("linkedWalletId") REFERENCES "Trader" ("id") ON DELETE RESTRICT ON UPDATE CASCADE
);

-- CreateTable
CREATE TABLE "TraderLabelAssignment" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "traderId" TEXT NOT NULL,
    "label" TEXT NOT NULL,
    "source" TEXT NOT NULL DEFAULT 'ALGORITHM',
    "confidence" REAL NOT NULL DEFAULT 0,
    "evidence" TEXT NOT NULL DEFAULT '[]',
    "assignedAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "expiresAt" DATETIME,
    CONSTRAINT "TraderLabelAssignment_traderId_fkey" FOREIGN KEY ("traderId") REFERENCES "Trader" ("id") ON DELETE RESTRICT ON UPDATE CASCADE
);

-- CreateTable
CREATE TABLE "Signal" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "type" TEXT NOT NULL DEFAULT 'CUSTOM',
    "tokenId" TEXT NOT NULL,
    "confidence" INTEGER NOT NULL DEFAULT 50,
    "priceTarget" REAL,
    "direction" TEXT NOT NULL DEFAULT 'LONG',
    "description" TEXT NOT NULL DEFAULT '',
    "metadata" TEXT NOT NULL DEFAULT '{}',
    "createdAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "Signal_tokenId_fkey" FOREIGN KEY ("tokenId") REFERENCES "Token" ("id") ON DELETE RESTRICT ON UPDATE CASCADE
);

-- CreateTable
CREATE TABLE "UserEvent" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "eventType" TEXT NOT NULL DEFAULT 'OPEN_POSITION',
    "tokenId" TEXT,
    "walletAddress" TEXT,
    "entryPrice" REAL,
    "stopLoss" REAL,
    "takeProfit" REAL,
    "pnl" REAL,
    "createdAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- CreateTable
CREATE TABLE "PatternRule" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "name" TEXT NOT NULL,
    "description" TEXT NOT NULL DEFAULT '',
    "category" TEXT NOT NULL DEFAULT 'GENERAL',
    "conditions" TEXT NOT NULL DEFAULT '{}',
    "isActive" BOOLEAN NOT NULL DEFAULT true,
    "backtestResults" TEXT NOT NULL DEFAULT '{}',
    "winRate" REAL NOT NULL DEFAULT 0,
    "occurrences" INTEGER NOT NULL DEFAULT 0,
    "createdAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- CreateTable
CREATE TABLE "TokenDNA" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "tokenId" TEXT NOT NULL,
    "liquidityDNA" TEXT NOT NULL DEFAULT '[]',
    "walletDNA" TEXT NOT NULL DEFAULT '[]',
    "topologyDNA" TEXT NOT NULL DEFAULT '[]',
    "riskScore" INTEGER NOT NULL DEFAULT 50,
    "botActivityScore" REAL NOT NULL DEFAULT 0,
    "smartMoneyScore" REAL NOT NULL DEFAULT 0,
    "retailScore" REAL NOT NULL DEFAULT 0,
    "whaleScore" REAL NOT NULL DEFAULT 0,
    "washTradeProb" REAL NOT NULL DEFAULT 0,
    "sniperPct" REAL NOT NULL DEFAULT 0,
    "mevPct" REAL NOT NULL DEFAULT 0,
    "copyBotPct" REAL NOT NULL DEFAULT 0,
    "traderComposition" TEXT NOT NULL DEFAULT '{}',
    "topWallets" TEXT NOT NULL DEFAULT '[]',
    "createdAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "TokenDNA_tokenId_fkey" FOREIGN KEY ("tokenId") REFERENCES "Token" ("id") ON DELETE RESTRICT ON UPDATE CASCADE
);

-- CreateTable
CREATE TABLE "PredictiveSignal" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "signalType" TEXT NOT NULL,
    "chain" TEXT NOT NULL DEFAULT 'SOL',
    "tokenAddress" TEXT,
    "sector" TEXT,
    "prediction" TEXT NOT NULL DEFAULT '{}',
    "direction" TEXT NOT NULL DEFAULT 'NEUTRAL',
    "confidence" REAL NOT NULL DEFAULT 0,
    "timeframe" TEXT NOT NULL DEFAULT '1h',
    "validUntil" DATETIME,
    "evidence" TEXT NOT NULL DEFAULT '[]',
    "historicalHitRate" REAL NOT NULL DEFAULT 0,
    "dataPointsUsed" INTEGER NOT NULL DEFAULT 0,
    "wasCorrect" BOOLEAN,
    "actualOutcome" TEXT,
    "createdAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" DATETIME NOT NULL
);

-- CreateTable
CREATE TABLE "TradingSystem" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "name" TEXT NOT NULL,
    "description" TEXT,
    "category" TEXT NOT NULL,
    "icon" TEXT NOT NULL DEFAULT '🎯',
    "assetFilter" TEXT NOT NULL DEFAULT '{}',
    "phaseConfig" TEXT NOT NULL DEFAULT '{}',
    "entrySignal" TEXT NOT NULL DEFAULT '{}',
    "executionConfig" TEXT NOT NULL DEFAULT '{}',
    "exitSignal" TEXT NOT NULL DEFAULT '{}',
    "bigDataContext" TEXT NOT NULL DEFAULT '{}',
    "primaryTimeframe" TEXT NOT NULL DEFAULT '1h',
    "confirmTimeframes" TEXT NOT NULL DEFAULT '[]',
    "maxPositionPct" REAL NOT NULL DEFAULT 5,
    "maxOpenPositions" INTEGER NOT NULL DEFAULT 10,
    "stopLossPct" REAL NOT NULL DEFAULT 15,
    "takeProfitPct" REAL NOT NULL DEFAULT 40,
    "trailingStopPct" REAL,
    "cashReservePct" REAL NOT NULL DEFAULT 20,
    "allocationMethod" TEXT NOT NULL DEFAULT 'KELLY_MODIFIED',
    "allocationConfig" TEXT NOT NULL DEFAULT '{}',
    "isActive" BOOLEAN NOT NULL DEFAULT false,
    "isPaperTrading" BOOLEAN NOT NULL DEFAULT false,
    "version" INTEGER NOT NULL DEFAULT 1,
    "parentSystemId" TEXT,
    "autoOptimize" BOOLEAN NOT NULL DEFAULT false,
    "optimizationMethod" TEXT,
    "optimizationFreq" TEXT,
    "totalBacktests" INTEGER NOT NULL DEFAULT 0,
    "bestSharpe" REAL NOT NULL DEFAULT 0,
    "bestWinRate" REAL NOT NULL DEFAULT 0,
    "bestPnlPct" REAL NOT NULL DEFAULT 0,
    "avgHoldTimeMin" REAL NOT NULL DEFAULT 0,
    "createdAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" DATETIME NOT NULL,
    CONSTRAINT "TradingSystem_parentSystemId_fkey" FOREIGN KEY ("parentSystemId") REFERENCES "TradingSystem" ("id") ON DELETE SET NULL ON UPDATE CASCADE
);

-- CreateTable
CREATE TABLE "BacktestRun" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "systemId" TEXT NOT NULL,
    "mode" TEXT NOT NULL DEFAULT 'HISTORICAL',
    "periodStart" DATETIME NOT NULL,
    "periodEnd" DATETIME NOT NULL,
    "initialCapital" REAL NOT NULL,
    "capitalAllocation" TEXT NOT NULL DEFAULT '{}',
    "allocationMethod" TEXT NOT NULL DEFAULT 'KELLY_MODIFIED',
    "finalCapital" REAL NOT NULL DEFAULT 0,
    "totalPnl" REAL NOT NULL DEFAULT 0,
    "totalPnlPct" REAL NOT NULL DEFAULT 0,
    "annualizedReturn" REAL,
    "benchmarkReturn" REAL,
    "alpha" REAL,
    "totalTrades" INTEGER NOT NULL DEFAULT 0,
    "winTrades" INTEGER NOT NULL DEFAULT 0,
    "lossTrades" INTEGER NOT NULL DEFAULT 0,
    "winRate" REAL NOT NULL DEFAULT 0,
    "avgWin" REAL NOT NULL DEFAULT 0,
    "avgLoss" REAL NOT NULL DEFAULT 0,
    "profitFactor" REAL NOT NULL DEFAULT 0,
    "expectancy" REAL NOT NULL DEFAULT 0,
    "maxDrawdown" REAL NOT NULL DEFAULT 0,
    "maxDrawdownPct" REAL NOT NULL DEFAULT 0,
    "sharpeRatio" REAL NOT NULL DEFAULT 0,
    "sortinoRatio" REAL,
    "calmarRatio" REAL,
    "recoveryFactor" REAL,
    "avgHoldTimeMin" REAL NOT NULL DEFAULT 0,
    "marketExposurePct" REAL NOT NULL DEFAULT 0,
    "phaseResults" TEXT NOT NULL DEFAULT '{}',
    "timeframeResults" TEXT NOT NULL DEFAULT '{}',
    "operationTypeResults" TEXT NOT NULL DEFAULT '{}',
    "allocationMethodResults" TEXT NOT NULL DEFAULT '{}',
    "optimizationEnabled" BOOLEAN NOT NULL DEFAULT false,
    "optimizationMethod" TEXT,
    "bestParameters" TEXT,
    "optimizationScore" REAL,
    "inSampleScore" REAL,
    "outOfSampleScore" REAL,
    "walkForwardRatio" REAL,
    "status" TEXT NOT NULL DEFAULT 'PENDING',
    "progress" REAL NOT NULL DEFAULT 0,
    "startedAt" DATETIME,
    "completedAt" DATETIME,
    "errorLog" TEXT,
    "createdAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "BacktestRun_systemId_fkey" FOREIGN KEY ("systemId") REFERENCES "TradingSystem" ("id") ON DELETE RESTRICT ON UPDATE CASCADE
);

-- CreateTable
CREATE TABLE "PriceCandle" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "tokenAddress" TEXT NOT NULL,
    "chain" TEXT NOT NULL DEFAULT 'SOL',
    "timeframe" TEXT NOT NULL,
    "timestamp" DATETIME NOT NULL,
    "open" REAL NOT NULL,
    "high" REAL NOT NULL,
    "low" REAL NOT NULL,
    "close" REAL NOT NULL,
    "volume" REAL NOT NULL,
    "trades" INTEGER NOT NULL DEFAULT 0,
    "source" TEXT NOT NULL DEFAULT 'coingecko',
    "createdAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "PriceCandle_tokenAddress_fkey" FOREIGN KEY ("tokenAddress") REFERENCES "Token" ("address") ON DELETE RESTRICT ON UPDATE CASCADE
);

-- CreateTable
CREATE TABLE "TokenLifecycleState" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "tokenAddress" TEXT NOT NULL,
    "chain" TEXT NOT NULL DEFAULT 'SOL',
    "phase" TEXT NOT NULL,
    "phaseProbability" REAL NOT NULL DEFAULT 0,
    "phaseDistribution" TEXT NOT NULL DEFAULT '{}',
    "transitionFrom" TEXT,
    "transitionProb" REAL,
    "signals" TEXT NOT NULL DEFAULT '{}',
    "detectedAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "TokenLifecycleState_tokenAddress_fkey" FOREIGN KEY ("tokenAddress") REFERENCES "Token" ("address") ON DELETE RESTRICT ON UPDATE CASCADE
);

-- CreateTable
CREATE TABLE "TraderBehaviorModel" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "archetype" TEXT NOT NULL,
    "tokenPhase" TEXT NOT NULL,
    "action" TEXT NOT NULL,
    "probability" REAL NOT NULL DEFAULT 0,
    "intensity" REAL NOT NULL DEFAULT 0,
    "duration" REAL NOT NULL DEFAULT 0,
    "observations" INTEGER NOT NULL DEFAULT 0,
    "confidence" REAL NOT NULL DEFAULT 0,
    "lastUpdated" DATETIME NOT NULL
);

-- CreateTable
CREATE TABLE "FeedbackMetrics" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "sourceType" TEXT NOT NULL,
    "sourceId" TEXT NOT NULL,
    "metricName" TEXT NOT NULL,
    "metricValue" REAL NOT NULL,
    "context" TEXT NOT NULL DEFAULT '{}',
    "period" TEXT NOT NULL DEFAULT '24h',
    "measuredAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- CreateTable
CREATE TABLE "SystemEvolution" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "parentSystemId" TEXT,
    "childSystemId" TEXT NOT NULL,
    "evolutionType" TEXT NOT NULL,
    "triggerMetric" TEXT NOT NULL,
    "triggerValue" REAL NOT NULL,
    "improvementPct" REAL NOT NULL DEFAULT 0,
    "backtestId" TEXT,
    "approvedAt" DATETIME,
    "createdAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- CreateTable
CREATE TABLE "ComparativeAnalysis" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "modelA" TEXT NOT NULL,
    "modelB" TEXT NOT NULL,
    "dimension" TEXT NOT NULL,
    "context" TEXT NOT NULL DEFAULT '{}',
    "metricsA" TEXT NOT NULL DEFAULT '{}',
    "metricsB" TEXT NOT NULL DEFAULT '{}',
    "winner" TEXT,
    "confidenceDiff" REAL NOT NULL DEFAULT 0,
    "measuredAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- CreateTable
CREATE TABLE "BrainCycleRun" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "cycleNumber" INTEGER NOT NULL DEFAULT 0,
    "capitalUsd" REAL NOT NULL DEFAULT 10,
    "initialCapitalUsd" REAL NOT NULL DEFAULT 10,
    "chain" TEXT NOT NULL DEFAULT 'SOL',
    "scanLimit" INTEGER NOT NULL DEFAULT 20,
    "status" TEXT NOT NULL DEFAULT 'RUNNING',
    "startedAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "completedAt" DATETIME,
    "tokensScanned" INTEGER NOT NULL DEFAULT 0,
    "tokensOperable" INTEGER NOT NULL DEFAULT 0,
    "tokensTradeable" INTEGER NOT NULL DEFAULT 0,
    "topPicks" TEXT NOT NULL DEFAULT '[]',
    "operabilitySummary" TEXT NOT NULL DEFAULT '{}',
    "capitalBeforeCycle" REAL NOT NULL DEFAULT 0,
    "capitalAfterCycle" REAL NOT NULL DEFAULT 0,
    "cyclePnlUsd" REAL NOT NULL DEFAULT 0,
    "cyclePnlPct" REAL NOT NULL DEFAULT 0,
    "cumulativeReturnPct" REAL NOT NULL DEFAULT 0,
    "phaseDistribution" TEXT NOT NULL DEFAULT '{}',
    "dominantRegime" TEXT NOT NULL DEFAULT 'SIDEWAYS',
    "regimeConfidence" REAL NOT NULL DEFAULT 0,
    "errorLog" TEXT,
    "cycleDurationMs" INTEGER NOT NULL DEFAULT 0,
    "createdAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- CreateTable
CREATE TABLE "OperabilitySnapshot" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "tokenAddress" TEXT NOT NULL,
    "symbol" TEXT NOT NULL,
    "chain" TEXT NOT NULL DEFAULT 'SOL',
    "overallScore" REAL NOT NULL DEFAULT 0,
    "liquidityScore" REAL NOT NULL DEFAULT 0,
    "feeScore" REAL NOT NULL DEFAULT 0,
    "slippageScore" REAL NOT NULL DEFAULT 0,
    "healthScore" REAL NOT NULL DEFAULT 0,
    "marginScore" REAL NOT NULL DEFAULT 0,
    "totalCostUsd" REAL NOT NULL DEFAULT 0,
    "totalCostPct" REAL NOT NULL DEFAULT 0,
    "slippagePct" REAL NOT NULL DEFAULT 0,
    "recommendedPositionUsd" REAL NOT NULL DEFAULT 0,
    "operabilityLevel" TEXT NOT NULL DEFAULT 'UNOPERABLE',
    "isOperable" BOOLEAN NOT NULL DEFAULT false,
    "minimumGainPct" REAL NOT NULL DEFAULT 0,
    "priceUsd" REAL NOT NULL DEFAULT 0,
    "liquidityUsd" REAL NOT NULL DEFAULT 0,
    "volume24h" REAL NOT NULL DEFAULT 0,
    "marketCap" REAL NOT NULL DEFAULT 0,
    "cycleRunId" TEXT,
    "warnings" TEXT NOT NULL DEFAULT '[]',
    "createdAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- CreateTable
CREATE TABLE "CompoundGrowthTracker" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "capitalUsd" REAL NOT NULL DEFAULT 0,
    "initialCapitalUsd" REAL NOT NULL DEFAULT 10,
    "totalReturnPct" REAL NOT NULL DEFAULT 0,
    "totalPnlUsd" REAL NOT NULL DEFAULT 0,
    "periodPnlUsd" REAL NOT NULL DEFAULT 0,
    "periodReturnPct" REAL NOT NULL DEFAULT 0,
    "periodTrades" INTEGER NOT NULL DEFAULT 0,
    "periodWins" INTEGER NOT NULL DEFAULT 0,
    "periodLosses" INTEGER NOT NULL DEFAULT 0,
    "totalFeesPaidUsd" REAL NOT NULL DEFAULT 0,
    "totalSlippageUsd" REAL NOT NULL DEFAULT 0,
    "feeAdjustedPnlUsd" REAL NOT NULL DEFAULT 0,
    "feeAdjustedReturnPct" REAL NOT NULL DEFAULT 0,
    "maxDrawdownPct" REAL NOT NULL DEFAULT 0,
    "sharpeRatio" REAL NOT NULL DEFAULT 0,
    "winRate" REAL NOT NULL DEFAULT 0,
    "dailyCompoundRate" REAL NOT NULL DEFAULT 0,
    "projectedAnnualReturn" REAL NOT NULL DEFAULT 0,
    "period" TEXT NOT NULL DEFAULT '1h',
    "measuredAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- CreateTable
CREATE TABLE "BacktestOperation" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "backtestId" TEXT NOT NULL,
    "systemId" TEXT NOT NULL,
    "tokenAddress" TEXT NOT NULL,
    "tokenSymbol" TEXT,
    "chain" TEXT NOT NULL,
    "tokenPhase" TEXT NOT NULL,
    "tokenAgeMinutes" REAL NOT NULL,
    "marketConditions" TEXT NOT NULL DEFAULT '{}',
    "tokenDnaSnapshot" TEXT NOT NULL DEFAULT '{}',
    "traderComposition" TEXT NOT NULL DEFAULT '{}',
    "bigDataContext" TEXT NOT NULL DEFAULT '{}',
    "operationType" TEXT NOT NULL,
    "timeframe" TEXT NOT NULL,
    "entryPrice" REAL NOT NULL,
    "entryTime" DATETIME NOT NULL,
    "entryReason" TEXT NOT NULL DEFAULT '{}',
    "exitPrice" REAL,
    "exitTime" DATETIME,
    "exitReason" TEXT,
    "quantity" REAL NOT NULL,
    "positionSizeUsd" REAL NOT NULL,
    "pnlUsd" REAL,
    "pnlPct" REAL,
    "holdTimeMin" REAL,
    "maxFavorableExc" REAL,
    "maxAdverseExc" REAL,
    "capitalAllocPct" REAL NOT NULL,
    "allocationMethodUsed" TEXT,
    "createdAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "BacktestOperation_backtestId_fkey" FOREIGN KEY ("backtestId") REFERENCES "BacktestRun" ("id") ON DELETE RESTRICT ON UPDATE CASCADE,
    CONSTRAINT "BacktestOperation_systemId_fkey" FOREIGN KEY ("systemId") REFERENCES "TradingSystem" ("id") ON DELETE RESTRICT ON UPDATE CASCADE
);

-- CreateTable
CREATE TABLE "operability_scores" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "tokenAddress" TEXT NOT NULL,
    "chain" TEXT NOT NULL,
    "score" REAL NOT NULL,
    "feeImpactPct" REAL NOT NULL,
    "slippageImpactPct" REAL NOT NULL,
    "liquidityUsd" REAL NOT NULL,
    "maxPositionUsd" REAL NOT NULL,
    "volume24h" REAL NOT NULL,
    "spreadPct" REAL NOT NULL,
    "isOperable" BOOLEAN NOT NULL,
    "reason" TEXT,
    "cycleId" TEXT,
    "computedAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- CreateTable
CREATE TABLE "trading_cycles" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "cycleNumber" INTEGER NOT NULL,
    "status" TEXT NOT NULL DEFAULT 'RUNNING',
    "tokensScanned" INTEGER NOT NULL DEFAULT 0,
    "tokensOperable" INTEGER NOT NULL DEFAULT 0,
    "tokensMatched" INTEGER NOT NULL DEFAULT 0,
    "signalsGenerated" INTEGER NOT NULL DEFAULT 0,
    "capitalBeforeUsd" REAL NOT NULL DEFAULT 0,
    "capitalAfterUsd" REAL NOT NULL DEFAULT 0,
    "feesPaidUsd" REAL NOT NULL DEFAULT 0,
    "netGainUsd" REAL NOT NULL DEFAULT 0,
    "netGainPct" REAL NOT NULL DEFAULT 0,
    "startedAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "completedAt" DATETIME,
    "error" TEXT
);

-- CreateTable
CREATE TABLE "capital_states" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "totalCapitalUsd" REAL NOT NULL,
    "allocatedUsd" REAL NOT NULL,
    "availableUsd" REAL NOT NULL,
    "feesPaidTotalUsd" REAL NOT NULL DEFAULT 0,
    "realizedPnlUsd" REAL NOT NULL DEFAULT 0,
    "unrealizedPnlUsd" REAL NOT NULL DEFAULT 0,
    "compoundGrowthPct" REAL NOT NULL DEFAULT 0,
    "cycleCount" INTEGER NOT NULL DEFAULT 0,
    "updatedAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAtCycle" INTEGER NOT NULL DEFAULT 0
);

-- CreateTable
CREATE TABLE "extraction_jobs" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "type" TEXT NOT NULL,
    "jobType" TEXT NOT NULL DEFAULT 'FULL',
    "status" TEXT NOT NULL DEFAULT 'PENDING',
    "chain" TEXT NOT NULL DEFAULT 'SOL',
    "startedAt" DATETIME,
    "completedAt" DATETIME,
    "error" TEXT,
    "recordsProcessed" INTEGER NOT NULL DEFAULT 0,
    "tokensDiscovered" INTEGER NOT NULL DEFAULT 0,
    "candlesStored" INTEGER NOT NULL DEFAULT 0,
    "walletsProfiled" INTEGER NOT NULL DEFAULT 0,
    "transactionsStored" INTEGER NOT NULL DEFAULT 0,
    "signalsGenerated" INTEGER NOT NULL DEFAULT 0,
    "protocolsStored" INTEGER NOT NULL DEFAULT 0,
    "sourcesUsed" TEXT NOT NULL DEFAULT '[]',
    "durationMs" INTEGER NOT NULL DEFAULT 0,
    "errors" TEXT NOT NULL DEFAULT '[]',
    "config" TEXT NOT NULL DEFAULT '{}',
    "metadata" TEXT NOT NULL DEFAULT '{}',
    "createdAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" DATETIME NOT NULL
);

-- CreateTable
CREATE TABLE "data_retention_policies" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "dataType" TEXT NOT NULL,
    "tableName" TEXT NOT NULL DEFAULT '',
    "retentionDays" INTEGER NOT NULL DEFAULT 30,
    "hotDays" INTEGER NOT NULL DEFAULT 7,
    "warmDays" INTEGER NOT NULL DEFAULT 30,
    "coldDays" INTEGER NOT NULL DEFAULT 90,
    "archiveMethod" TEXT NOT NULL DEFAULT 'DELETE',
    "compressionEnabled" BOOLEAN NOT NULL DEFAULT true,
    "aggregationInterval" TEXT,
    "lastCleanupAt" DATETIME,
    "lastArchivedAt" DATETIME,
    "isActive" BOOLEAN NOT NULL DEFAULT true,
    "lastArchiveStats" TEXT NOT NULL DEFAULT '{}',
    "createdAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" DATETIME NOT NULL
);

-- CreateTable
CREATE TABLE "api_rate_limits" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "service" TEXT NOT NULL,
    "maxRequests" INTEGER NOT NULL DEFAULT 300,
    "windowMs" INTEGER NOT NULL DEFAULT 60000,
    "currentCount" INTEGER NOT NULL DEFAULT 0,
    "windowStart" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "createdAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" DATETIME NOT NULL
);

-- CreateTable
CREATE TABLE "decision_logs" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "systemId" TEXT,
    "tokenAddress" TEXT,
    "chain" TEXT,
    "tokenSymbol" TEXT,
    "decisionType" TEXT NOT NULL DEFAULT 'SYSTEM_MATCH',
    "decision" TEXT NOT NULL,
    "recommendedSystem" TEXT,
    "confidence" REAL NOT NULL DEFAULT 0,
    "dataQualityScore" REAL NOT NULL DEFAULT 0,
    "reasoning" TEXT NOT NULL DEFAULT '{}',
    "outcome" TEXT,
    "pnlPct" REAL,
    "tokenPhaseAtDecision" TEXT,
    "regimeAtDecision" TEXT,
    "operabilityAtDecision" REAL,
    "wasActedUpon" BOOLEAN NOT NULL DEFAULT false,
    "realizedPnlPct" REAL,
    "decisionWasCorrect" BOOLEAN,
    "realizedPnlUsd" REAL,
    "smartMoneySignal" TEXT,
    "decidedAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "createdAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- CreateTable
CREATE TABLE "protocol_data" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "protocol" TEXT NOT NULL,
    "chain" TEXT NOT NULL DEFAULT 'SOL',
    "slug" TEXT NOT NULL DEFAULT '',
    "slug_chain" TEXT,
    "tvl" REAL NOT NULL DEFAULT 0,
    "tvlUsd" REAL NOT NULL DEFAULT 0,
    "volume24h" REAL NOT NULL DEFAULT 0,
    "fees24h" REAL NOT NULL DEFAULT 0,
    "metadata" TEXT NOT NULL DEFAULT '{}',
    "createdAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" DATETIME NOT NULL
);

-- CreateIndex
CREATE UNIQUE INDEX "Token_address_key" ON "Token"("address");

-- CreateIndex
CREATE UNIQUE INDEX "Trader_address_key" ON "Trader"("address");

-- CreateIndex
CREATE UNIQUE INDEX "TraderTransaction_txHash_key" ON "TraderTransaction"("txHash");

-- CreateIndex
CREATE UNIQUE INDEX "TokenDNA_tokenId_key" ON "TokenDNA"("tokenId");

-- CreateIndex
CREATE INDEX "PriceCandle_tokenAddress_timeframe_timestamp_idx" ON "PriceCandle"("tokenAddress", "timeframe", "timestamp");

-- CreateIndex
CREATE INDEX "PriceCandle_chain_timeframe_timestamp_idx" ON "PriceCandle"("chain", "timeframe", "timestamp");

-- CreateIndex
CREATE UNIQUE INDEX "PriceCandle_tokenAddress_chain_timeframe_timestamp_key" ON "PriceCandle"("tokenAddress", "chain", "timeframe", "timestamp");

-- CreateIndex
CREATE INDEX "TokenLifecycleState_tokenAddress_detectedAt_idx" ON "TokenLifecycleState"("tokenAddress", "detectedAt");

-- CreateIndex
CREATE INDEX "TokenLifecycleState_phase_detectedAt_idx" ON "TokenLifecycleState"("phase", "detectedAt");

-- CreateIndex
CREATE INDEX "TraderBehaviorModel_archetype_tokenPhase_idx" ON "TraderBehaviorModel"("archetype", "tokenPhase");

-- CreateIndex
CREATE UNIQUE INDEX "TraderBehaviorModel_archetype_tokenPhase_action_key" ON "TraderBehaviorModel"("archetype", "tokenPhase", "action");

-- CreateIndex
CREATE INDEX "FeedbackMetrics_sourceType_metricName_measuredAt_idx" ON "FeedbackMetrics"("sourceType", "metricName", "measuredAt");

-- CreateIndex
CREATE INDEX "FeedbackMetrics_sourceType_sourceId_idx" ON "FeedbackMetrics"("sourceType", "sourceId");

-- CreateIndex
CREATE INDEX "SystemEvolution_parentSystemId_idx" ON "SystemEvolution"("parentSystemId");

-- CreateIndex
CREATE INDEX "SystemEvolution_evolutionType_idx" ON "SystemEvolution"("evolutionType");

-- CreateIndex
CREATE INDEX "ComparativeAnalysis_dimension_measuredAt_idx" ON "ComparativeAnalysis"("dimension", "measuredAt");

-- CreateIndex
CREATE INDEX "ComparativeAnalysis_modelA_modelB_idx" ON "ComparativeAnalysis"("modelA", "modelB");

-- CreateIndex
CREATE INDEX "BrainCycleRun_status_startedAt_idx" ON "BrainCycleRun"("status", "startedAt");

-- CreateIndex
CREATE INDEX "BrainCycleRun_cycleNumber_idx" ON "BrainCycleRun"("cycleNumber");

-- CreateIndex
CREATE INDEX "OperabilitySnapshot_tokenAddress_createdAt_idx" ON "OperabilitySnapshot"("tokenAddress", "createdAt");

-- CreateIndex
CREATE INDEX "OperabilitySnapshot_operabilityLevel_createdAt_idx" ON "OperabilitySnapshot"("operabilityLevel", "createdAt");

-- CreateIndex
CREATE INDEX "OperabilitySnapshot_isOperable_createdAt_idx" ON "OperabilitySnapshot"("isOperable", "createdAt");

-- CreateIndex
CREATE INDEX "CompoundGrowthTracker_measuredAt_idx" ON "CompoundGrowthTracker"("measuredAt");

-- CreateIndex
CREATE INDEX "CompoundGrowthTracker_period_measuredAt_idx" ON "CompoundGrowthTracker"("period", "measuredAt");

-- CreateIndex
CREATE INDEX "operability_scores_tokenAddress_computedAt_idx" ON "operability_scores"("tokenAddress", "computedAt");

-- CreateIndex
CREATE INDEX "operability_scores_isOperable_score_idx" ON "operability_scores"("isOperable", "score");

-- CreateIndex
CREATE INDEX "trading_cycles_status_startedAt_idx" ON "trading_cycles"("status", "startedAt");

-- CreateIndex
CREATE UNIQUE INDEX "data_retention_policies_dataType_key" ON "data_retention_policies"("dataType");

-- CreateIndex
CREATE UNIQUE INDEX "api_rate_limits_service_key" ON "api_rate_limits"("service");

-- CreateIndex
CREATE UNIQUE INDEX "protocol_data_slug_chain_key" ON "protocol_data"("slug_chain");
