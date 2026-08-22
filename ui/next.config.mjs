/** @type {import('next').NextConfig} */
// Reverse-proxy sub-path mounting (embedded mode): when NEXT_PUBLIC_BASE_PATH is
// set (e.g. "/assistant"), the app and its assets serve under that prefix so a
// host application can proxy portal.example.com/assistant/* to this UI. Blank
// keeps the standalone behavior unchanged.
//
// The Content-Security-Policy is deliberately NOT here. It carries a per-request script nonce,
// which this static table cannot express, so it is built in `lib/csp.mjs` and emitted once from
// `proxy.ts`. Emitting it from both layers would hand the browser two policies to intersect, and
// the stricter one wins per directive, which is exactly how the un-hydratable console came back
// the first time somebody "fixed" it in two places.
import { readFileSync } from "node:fs";

import { assertHydratableCsp } from "./lib/csp.mjs";

// Runs at module scope, which `next build` and `next start` both evaluate, so a console whose
// nonce policy and rendering mode disagree never comes up at all.
assertHydratableCsp(readFileSync(new URL("./app/layout.tsx", import.meta.url), "utf8"));

const basePath = process.env.NEXT_PUBLIC_BASE_PATH || "";

const nextConfig = {
  reactStrictMode: true,
  ...(basePath ? { basePath, assetPrefix: basePath } : {}),
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          // Only the headers a static table CAN express. Anything per-request lives in proxy.ts.
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "no-referrer" },
        ],
      },
    ];
  },
};

export default nextConfig;
