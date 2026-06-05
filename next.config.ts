import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  typescript: {
    ignoreBuildErrors: true,
  },
  reactStrictMode: false,
  // Expose NextAuth env vars to the Edge Runtime (proxy/middleware)
  env: {
    NEXTAUTH_URL: process.env.NEXTAUTH_URL,
    NEXTAUTH_SECRET: process.env.NEXTAUTH_SECRET,
    AUTH_ENABLED: process.env.AUTH_ENABLED || 'false',
    NEXT_PUBLIC_AUTH_ENABLED: process.env.AUTH_ENABLED || 'false',
  },
};

export default nextConfig;
