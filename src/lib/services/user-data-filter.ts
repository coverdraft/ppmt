import { getServerSession } from 'next-auth';
import { authOptions } from '@/lib/auth';
import { db } from '@/lib/db';

// 🔓 Auth disabled — cached demo user ID for performance
let _cachedDemoUserId: string | null = null;

const DEMO_EMAIL = 'demo@cryptoquant.com';

/**
 * Get the current authenticated user ID from the server session.
 * When auth is disabled (no session), falls back to the demo user ID from DB.
 * Auto-creates the demo user if it doesn't exist, so this ALWAYS returns a valid ID.
 */
export async function getCurrentUserId(): Promise<string> {
  // Try session first
  try {
    const session = await getServerSession(authOptions);
    if (session?.user) {
      return (session.user as Record<string, unknown>).id as string;
    }
  } catch {
    // NextAuth not configured — fall through to demo user
  }

  // No session — fall back to demo user (cached)
  if (_cachedDemoUserId) {
    return _cachedDemoUserId;
  }

  // Look up demo user in DB
  const existing = await db.user.findUnique({
    where: { email: DEMO_EMAIL },
    select: { id: true },
  });

  if (existing) {
    _cachedDemoUserId = existing.id;
    return _cachedDemoUserId;
  }

  // Auto-create demo user if it doesn't exist
  try {
    const { hash } = await import('bcryptjs');
    const passwordHash = await hash('demo', 10);
    const newUser = await db.user.create({
      data: {
        email: DEMO_EMAIL,
        name: 'Demo User',
        role: 'ADMIN',
        passwordHash,
      },
    });
    _cachedDemoUserId = newUser.id;
    console.log(`[Auth] Auto-created demo user: ${newUser.id}`);
    return _cachedDemoUserId;
  } catch (createError) {
    // Race condition: another request might have created it between our find and create
    const retry = await db.user.findUnique({
      where: { email: DEMO_EMAIL },
      select: { id: true },
    });
    if (retry) {
      _cachedDemoUserId = retry.id;
      return _cachedDemoUserId;
    }
    // This should never happen, but as a last resort:
    console.error('[Auth] CRITICAL: Cannot create or find demo user:', createError);
    throw new Error('Cannot initialize demo user');
  }
}

/**
 * Get the current authenticated user session.
 * Returns null if not authenticated.
 */
export async function getCurrentSession() {
  return getServerSession(authOptions);
}

/**
 * Build a Prisma where clause that filters data by userId.
 * For shared/built-in data (userId=null), include those too.
 * 
 * Usage:
 *   const where = { ...userScope(userId), category: 'ALPHA_HUNTER' };
 *   const results = await db.tradingSystem.findMany({ where });
 */
export function userScope(userId: string): { OR: Array<{ userId: string } | { userId: { equals: null } }> } {
  return {
    OR: [
      { userId },
      { userId: { equals: null } },
    ],
  };
}

/**
 * Strict user scope — only the user's own data (no shared/null).
 * Use for creating records or when shared data should NOT be visible.
 */
export function strictUserScope(userId: string): { userId: string } {
  return { userId };
}

/**
 * Template-specific scope:
 * - Built-in templates (isBuiltIn=true) are visible to all users
 * - User-created templates (isBuiltIn=false) are filtered by userId
 */
export function templateScope(userId: string): {
  OR: Array<{ isBuiltIn: boolean } | { userId: string }>;
} {
  return {
    OR: [
      { isBuiltIn: true },
      { userId },
    ],
  };
}
