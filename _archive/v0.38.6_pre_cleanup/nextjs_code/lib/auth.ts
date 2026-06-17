/**
 * Auth stub — next-auth has been removed.
 * Authentication is disabled; getCurrentUserId() creates/returns a demo user.
 * This file exists solely to satisfy any remaining imports.
 */

export const authOptions = {
  providers: [],
  session: { strategy: 'jwt' as const },
  secret: process.env.NEXTAUTH_SECRET,
};

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export default function NextAuthStub() {
  return {
    handlers: { GET: () => Response.json({ ok: true }), POST: () => Response.json({ ok: true }) },
  };
}
