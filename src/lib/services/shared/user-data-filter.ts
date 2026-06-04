import { db } from '@/lib/db';

// 🔓 Auth disabled — next-auth removed, cached demo user ID for performance
let _cachedDemoUserId: string | null = null;

const DEMO_EMAIL = 'demo@cryptoquant.com';

/**
 * Get the current user ID.
 * Since auth is disabled, this always returns the demo user ID.
 * Auto-creates the demo user if it doesn't exist, so this ALWAYS returns a valid ID.
 */
export async function getCurrentUserId(): Promise<string> {
  // Return cached demo user ID
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
    console.error('[Auth] CRITICAL: Cannot create or find demo user:', createError);
    throw new Error('Cannot initialize demo user');
  }
}

/**
 * Get the current authenticated user session.
 * Returns null since auth is disabled.
 */
export async function getCurrentSession() {
  return null;
}

/**
 * Build a Prisma where clause that filters data by userId.
 * For shared/built-in data (userId=null), include those too.
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
