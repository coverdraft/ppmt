/**
 * Auth route stub — next-auth removed.
 * Returns a simple JSON response so the route doesn't break.
 */
import { NextResponse } from 'next/server';

export function GET() {
  return NextResponse.json({ auth: 'disabled', message: 'Auth is disabled. Use getCurrentUserId() for demo user.' });
}

export function POST() {
  return NextResponse.json({ auth: 'disabled', message: 'Auth is disabled. Use getCurrentUserId() for demo user.' });
}
