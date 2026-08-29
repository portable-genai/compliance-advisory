import type { Metadata } from "next";
import "./globals.css";

// Required by the nonce CSP, not a performance preference. `proxy.ts` mints a per-request script
// nonce, and Next can only stamp it onto the script tags of a DYNAMICALLY rendered route. A
// statically prerendered page is built before the nonce exists, so every script tag would ship
// bare while the header advertises a nonce, and `'strict-dynamic'` turns off the `'self'` fallback
// that was at least loading the chunks. `assertHydratableCsp` in next.config.mjs refuses a build
// that drops this line.
export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Compliance Assistant",
  description:
    "Grounded RAG + agentic assistant for Compliance/Risk teams at APAC banks (MAS / HKMA / APRA / FSA).",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  // EMBED mode (NEXT_PUBLIC_EMBED=1): the host application owns the page chrome,
  // so render the console bare (page.tsx also drops its own top bar). Standalone
  // keeps the full-height body.
  const embed = process.env.NEXT_PUBLIC_EMBED === "1";
  return (
    <html lang="en">
      <body className={embed ? undefined : "min-h-screen"}>{children}</body>
    </html>
  );
}
