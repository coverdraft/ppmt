'use client';

import { createContext, useContext, type ReactNode } from 'react';

// 🔓 AUTH DISABLED — No login, no session, no redirect.
// This provider always returns a demo user and passes children through.
// To re-enable auth, restore the original UserProvider with SessionProvider.

interface UserInfo {
  id: string;
  email: string;
  name: string | null;
  image: string | null;
  role: string;
}

interface UserContextValue {
  user: UserInfo | null;
  isLoading: boolean;
  isAuthenticated: boolean;
}

const DEMO_USER: UserInfo = {
  id: 'demo-user',
  email: 'demo@cryptoquant.com',
  name: 'Demo User',
  image: null,
  role: 'ADMIN',
};

const UserContext = createContext<UserContextValue>({
  user: DEMO_USER,
  isLoading: false,
  isAuthenticated: true,
});

export function useUser() {
  return useContext(UserContext);
}

export function UserProvider({ children }: { children: ReactNode }) {
  return (
    <UserContext.Provider value={{ user: DEMO_USER, isLoading: false, isAuthenticated: true }}>
      {children}
    </UserContext.Provider>
  );
}
