/**
 * Startup Hook - CryptoQuant Terminal
 *
 * Auto-starts the brain scheduler when the Next.js server starts,
 * if it was previously running before the server was restarted.
 *
 * This ensures continuity: the user's laptop won't be running 24/7,
 * so when they start the server again, the brain picks up where it left off.
 */

let startupTriggered = false;

/**
 * Call this from a client component or API route to trigger the auto-start check.
 * It's idempotent - calling it multiple times is safe.
 */
export async function triggerAutoStart(): Promise<{
  autoStarted: boolean;
  wasPreviouslyRunning: boolean;
  message: string;
}> {
  if (startupTriggered) {
    return { autoStarted: false, wasPreviouslyRunning: false, message: 'Startup already triggered' };
  }
  startupTriggered = true;

  try {
    const { brainScheduler } = await import('./services/brain/brain-scheduler');
    const previousState = await brainScheduler.getPreviousState();

    if (previousState.wasRunning && previousState.config) {
      console.log('[Startup] Scheduler was previously RUNNING - auto-starting with saved config...');

      const result = await brainScheduler.start(previousState.config);

      if (result.started) {
        console.log(`[Startup] Auto-start successful: ${result.message}`);
        return {
          autoStarted: true,
          wasPreviouslyRunning: true,
          message: `Auto-started: ${result.message}`,
        };
      } else {
        console.warn(`[Startup] Auto-start failed: ${result.message}`);
        return {
          autoStarted: false,
          wasPreviouslyRunning: true,
          message: `Auto-start failed: ${result.message}`,
        };
      }
    }

    console.log('[Startup] Scheduler was not previously running - waiting for manual start');
    return {
      autoStarted: false,
      wasPreviouslyRunning: false,
      message: 'Scheduler was not previously running',
    };
  } catch (error) {
    console.warn('[Startup] Auto-start check failed:', error);
    // Reset so subsequent calls can retry
    startupTriggered = false;
    return {
      autoStarted: false,
      wasPreviouslyRunning: false,
      message: `Startup check failed: ${error instanceof Error ? error.message : String(error)}`,
    };
  }
}
