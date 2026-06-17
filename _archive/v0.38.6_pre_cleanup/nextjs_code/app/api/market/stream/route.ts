export const runtime = 'nodejs';

export async function GET() {
  const encoder = new TextEncoder();

  const stream = new ReadableStream({
    start(controller) {
      const sendEvent = (data: unknown) => {
        controller.enqueue(
          encoder.encode(`data: ${JSON.stringify(data)}\n\n`)
        );
      };

      // Send initial connection event
      sendEvent({ type: 'connected', timestamp: Date.now() });

      // Simulate market data updates every 3 seconds
      const interval = setInterval(() => {
        sendEvent({
          type: 'market-update',
          data: {
            btcPrice: 67500 + (Math.random() - 0.5) * 1000,
            ethPrice: 3450 + (Math.random() - 0.5) * 100,
            totalMarketCap: 2.45e12 + (Math.random() - 0.5) * 1e10,
            fearGreedIndex: Math.floor(Math.random() * 100),
            volume24h: 89.5e9 + (Math.random() - 0.5) * 5e9,
          },
          timestamp: Date.now(),
        });
      }, 3000);

      // Clean up on close
      setTimeout(() => {
        clearInterval(interval);
        controller.close();
      }, 300000); // 5 minute timeout
    },
  });

  return new Response(stream, {
    headers: {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      'Connection': 'keep-alive',
    },
  });
}
