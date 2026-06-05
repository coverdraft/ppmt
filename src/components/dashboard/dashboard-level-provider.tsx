'use client';

import React, { createContext, useContext, useEffect, useState, useCallback } from 'react';

// ============================================================
// TYPES
// ============================================================

export type DashboardLevel = 'executive' | 'professional' | 'engineer';

interface DashboardLevelContextValue {
  level: DashboardLevel;
  setLevel: (level: DashboardLevel) => void;
}

// ============================================================
// CONTEXT
// ============================================================

const DashboardLevelContext = createContext<DashboardLevelContextValue>({
  level: 'engineer',
  setLevel: () => {},
});

const STORAGE_KEY = 'cryptoquant-dashboard-level';

// ============================================================
// HELPER: Read level from localStorage (safe)
// ============================================================

function readStoredLevel(): DashboardLevel | null {
  if (typeof window === 'undefined') return null;
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === 'executive' || stored === 'professional' || stored === 'engineer') {
      return stored;
    }
  } catch {
    // localStorage unavailable
  }
  return null;
}

// ============================================================
// PROVIDER COMPONENT
// ============================================================

export function DashboardLevelProvider({ children }: { children: React.ReactNode }) {
  const [level, setLevelState] = useState<DashboardLevel>('engineer');

  // Sync from localStorage on mount — this must be in an effect to avoid
  // hydration mismatch (server always renders 'engineer')
  useEffect(() => {
    const stored = readStoredLevel();
    if (stored) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- intentional: sync from localStorage on mount to avoid hydration mismatch
      setLevelState(stored);
    }
  }, []);

  const setLevel = useCallback((newLevel: DashboardLevel) => {
    setLevelState(newLevel);
    try {
      localStorage.setItem(STORAGE_KEY, newLevel);
    } catch {
      // localStorage unavailable
    }
  }, []);

  // Keyboard shortcuts: Ctrl+1/2/3
  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      // Don't capture when typing in inputs
      if (
        e.target instanceof HTMLInputElement ||
        e.target instanceof HTMLTextAreaElement ||
        e.target instanceof HTMLSelectElement
      ) {
        return;
      }

      if (e.ctrlKey || e.metaKey) {
        if (e.key === '1') {
          e.preventDefault();
          setLevel('executive');
        } else if (e.key === '2') {
          e.preventDefault();
          setLevel('professional');
        } else if (e.key === '3') {
          e.preventDefault();
          setLevel('engineer');
        }
      }
    }

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [setLevel]);

  return (
    <DashboardLevelContext.Provider value={{ level, setLevel }}>
      {children}
    </DashboardLevelContext.Provider>
  );
}

// ============================================================
// HOOK
// ============================================================

export function useDashboardLevel() {
  const context = useContext(DashboardLevelContext);
  if (!context) {
    throw new Error('useDashboardLevel must be used within a DashboardLevelProvider');
  }
  return context;
}
