/** @type {import('next').NextConfig} */
const nextConfig = {
  // The API base is read client-side from NEXT_PUBLIC_API_URL (see lib/api.ts).
  reactStrictMode: true,
  ...(process.env.NEXT_DIST_DIR ? { distDir: process.env.NEXT_DIST_DIR } : {}),
};

export default nextConfig;
