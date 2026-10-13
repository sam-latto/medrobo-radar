import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  cacheComponents: true,
  partialPrefetching: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
  // The app shell sends users with no followed industries on to /industries
  async redirects() {
    return [{ source: "/", destination: "/briefing", permanent: false }];
  },
};

export default nextConfig;
