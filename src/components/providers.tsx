'use client';

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useState, useEffect } from 'react';

// 🔓 SessionProvider from next-auth REMOVED — no more login redirects
// To re-enable auth, wrap with SessionProvider again

/**
 * StartupInitializer - Triggers the auto-start check for the brain scheduler
 * when the app loads. This ensures that if the scheduler was previously running
 * before the server restarted, it will automatically resume.
 */
function StartupInitializer() {
  useEffect(() => {
    // Fire-and-forget: auto-start the scheduler if it was previously running
    fetch('/api/brain/scheduler', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'auto_start' }),
    })
      .then(res => res.json())
      .then(data => {
        if (data.success && data.data?.autoStarted) {
          console.log('[StartupInitializer] Scheduler auto-started:', data.data.message);
        }
      })
      .catch(err => {
        // Silently ignore - startup is best-effort
        console.warn('[StartupInitializer] Auto-start check failed:', err);
      });
  }, []);

  return null; // Renders nothing
}

export function Providers({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 60000,       // 60s stale time
            refetchInterval: 120000, // 2 min refetch
            retry: 1,               // Only retry once on failure
            retryDelay: 3000,       // Wait 3s before retry
          },
        },
      })
  );

  return (
    <QueryClientProvider client={queryClient}>
      <StartupInitializer />
      {children}
    </QueryClientProvider>
  );
}
