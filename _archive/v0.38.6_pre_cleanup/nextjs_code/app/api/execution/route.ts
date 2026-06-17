import { NextRequest, NextResponse } from 'next/server';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/**
 * GET /api/execution
 */
export async function GET() {
  try {
    const teModule = await import('@/lib/services/execution/trade-execution-engine');
    const tradeExecutionEngine = teModule.tradeExecutionEngine;
    const config = tradeExecutionEngine.getConfig();
    const stats = tradeExecutionEngine.getExecutionStats();
    const wallets = tradeExecutionEngine.getActiveWallets();
    const orders = tradeExecutionEngine.getOrders();

    return NextResponse.json({
      data: {
        config: {
          mode: config.mode,
          killSwitch: config.killSwitch,
          maxOpenPositions: config.maxOpenPositions,
          maxTradeSizeUsd: config.maxTradeSizeUsd,
          maxDailyLossUsd: config.maxDailyLossUsd,
          maxDailyLossPct: config.maxDailyLossPct,
          requireConfirmation: config.requireConfirmation,
          enabledChains: config.enabledChains,
          dexRoutes: config.dexRoutes,
        },
        stats,
        wallets: wallets.map(w => ({
          address: w.address,
          chain: w.chain,
          label: w.label,
          maxAllocationUsd: w.maxAllocationUsd,
          isActive: w.isActive,
        })),
        pendingOrders: orders.filter(o => o.status === 'PENDING').length,
        totalOrders: orders.length,
      },
    });
  } catch (error) {
    console.error('Error getting execution status:', error);
    return NextResponse.json(
      { data: null, error: 'Failed to get execution status' },
      { status: 500 },
    );
  }
}

/**
 * POST /api/execution
 * Execute various actions on the trade execution engine.
 *
 * Body params:
 * - action: 'add_wallet' | 'remove_wallet' | 'kill_switch' | 'resume' | 'update_config' | 'create_order' | 'submit_order' | 'cancel_order' | 'execute_order'
 * - Additional params depend on the action
 */
export async function POST(request: NextRequest) {
  try {
    const teModule = await import('@/lib/services/execution/trade-execution-engine');
    const tradeExecutionEngine = teModule.tradeExecutionEngine;
    const body = await request.json();
    const { action, ...params } = body as Record<string, unknown>;

    switch (action) {
      case 'add_wallet': {
        const { address, chain, label, maxAllocationUsd } = params as {
          address: string; chain: string; label: string; maxAllocationUsd: number;
        };
        if (!address || !chain || !label) {
          return NextResponse.json(
            { data: null, error: 'address, chain, and label are required' },
            { status: 400 },
          );
        }
        await tradeExecutionEngine.addWallet({
          address,
          chain,
          label,
          maxAllocationUsd: maxAllocationUsd || 100,
          isActive: true,
        });
        return NextResponse.json({ data: { added: true, address } });
      }

      case 'remove_wallet': {
        const { address } = params as { address: string };
        if (!address) {
          return NextResponse.json(
            { data: null, error: 'address is required' },
            { status: 400 },
          );
        }
        await tradeExecutionEngine.removeWallet(address);
        return NextResponse.json({ data: { removed: true, address } });
      }

      case 'kill_switch': {
        const { reason } = params as { reason?: string };
        tradeExecutionEngine.activateKillSwitch(reason || 'Manual activation via API');
        return NextResponse.json({ data: { killSwitchActive: true, reason } });
      }

      case 'resume': {
        tradeExecutionEngine.deactivateKillSwitch();
        return NextResponse.json({ data: { killSwitchActive: false, resumed: true } });
      }

      case 'update_config': {
        const { config: configUpdates } = params as { config: Record<string, unknown> };
        tradeExecutionEngine.updateConfig(configUpdates);
        return NextResponse.json({ data: { updated: true } });
      }

      case 'risk_check': {
        const { orderId } = params as { orderId: string };
        if (!orderId) {
          return NextResponse.json(
            { data: null, error: 'orderId is required' },
            { status: 400 },
          );
        }
        const order = tradeExecutionEngine.getOrder(orderId);
        if (!order) {
          return NextResponse.json(
            { data: null, error: 'Order not found' },
            { status: 404 },
          );
        }
        const riskResult = await tradeExecutionEngine.runRiskChecks(order);
        return NextResponse.json({ data: riskResult });
      }

      default:
        return NextResponse.json(
          { data: null, error: `Invalid action: ${action}. Supported: add_wallet, remove_wallet, kill_switch, resume, update_config, risk_check` },
          { status: 400 },
        );
    }
  } catch (error) {
    console.error('Error with execution action:', error);
    return NextResponse.json(
      { data: null, error: 'Failed to execute action' },
      { status: 500 },
    );
  }
}
