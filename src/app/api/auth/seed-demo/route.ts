import { NextResponse } from 'next/server';
import { db } from '@/lib/db';
import { hash } from 'bcryptjs';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/**
 * POST /api/auth/seed-demo
 * Seeds the demo user if it doesn't exist. Called on first app load.
 */
export async function POST() {
  try {
    const demoEmail = 'demo@cryptoquant.com';
    const demoPassword = 'demo';

    // Check if demo user already exists
    const existing = await db.user.findUnique({
      where: { email: demoEmail },
    });

    if (existing) {
      return NextResponse.json({ data: { message: 'Demo user already exists', userId: existing.id } });
    }

    // Create demo user
    const passwordHash = await hash(demoPassword, 10);
    const user = await db.user.create({
      data: {
        email: demoEmail,
        name: 'Demo User',
        role: 'ADMIN',
        passwordHash,
      },
    });

    return NextResponse.json({ data: { message: 'Demo user created', userId: user.id } }, { status: 201 });
  } catch (error) {
    console.error('Error seeding demo user:', error);
    return NextResponse.json({ error: 'Failed to seed demo user' }, { status: 500 });
  }
}
