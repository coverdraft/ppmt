import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  typescript: {
    ignoreBuildErrors: true,
  },
  reactStrictMode: false,
  allowedDevOrigins: [
    '.space-z.ai',
  ],
  async rewrites() {
    return [
      {
        source: "/bridge/:path*",
        destination: "http://localhost:3003/:path*",
      },
    ];
  },
};

export default nextConfig;
