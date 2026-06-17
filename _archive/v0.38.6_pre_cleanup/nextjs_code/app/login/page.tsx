'use client';

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';

// 🔓 Auth disabled — /login redirects to home immediately
export default function LoginPage() {
  const router = useRouter();

  useEffect(() => {
    router.replace('/');
  }, [router]);

  return null;
}
