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
  // `next dev` writes AGENTS.md and CLAUDE.md into this directory unless this is false; the
  // writer is node_modules/next/dist/server/lib/generate-agent-files.js. This repo's working
  // agreement is the AGENTS.md at its root and there is no tool-specific alias of it, so a
  // second one here is a second agreement to keep in step and CLAUDE.md is precisely the alias
  // the convention forbids. The generated prose also carries an em-dash, which the catalog's
  // house style forbids in shipped markdown. tests/unit/test_ui_agent_documents.py fails the
  // gate if this line goes away or if either file turns up on disk anyway.
  agentRules: false,
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
