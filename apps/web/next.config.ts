import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  images: { unoptimized: true },
  experimental: { cpus: 2 },
  async redirects() {
    return [
      {source: "/catalog", destination: "/it/catalog", permanent: true},
      {source: "/creators", destination: "/it/creators", permanent: true},
      {source: "/workspace", destination: "/it/workspace/send", permanent: true},
      {source: "/overview", destination: "/it/workspace/send", permanent: true},
      {source: "/results", destination: "/it/workspace/history", permanent: true},
      {source: "/settings", destination: "/ops/accounts?market=it", permanent: true},
    ];
  },
  /* config options here */
  webpack(config) {
    config.cache = false;
    config.module.rules.push({
      test: /\.svg$/,
      use: ["@svgr/webpack"],
    });
    return config;
  },

    turbopack: {
      rules: {
        '*.svg': {
          loaders: ['@svgr/webpack'],
          as: '*.js',
        },
      },
    },

};

export default nextConfig;
