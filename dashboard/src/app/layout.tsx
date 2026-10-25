import type { Metadata } from "next";
import Link from "next/link";

import "./globals.css";

export const metadata: Metadata = {
  title: "Fraud Defense Dashboard",
  description: "Self-red-teaming fraud defense at the x402 facilitator: traffic, curves, arena and controls.",
};

const NAV = [
  { href: "/", label: "Overview" },
  { href: "/stream", label: "Transaction stream" },
  { href: "/attackers", label: "Attacker drift" },
  { href: "/arena-3d", label: "3D arena" },
];

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full">
        <header className="h-12 border-b border-line bg-surface">
          <div className="mx-auto flex h-full max-w-6xl items-center gap-x-6 px-4">
            <span className="shrink-0 font-semibold">Fraud defense</span>
            <nav className="flex gap-4 overflow-x-auto whitespace-nowrap text-sm text-ink-2">
              {NAV.map((n) => (
                <Link key={n.href} href={n.href} className="hover:text-ink">
                  {n.label}
                </Link>
              ))}
            </nav>
          </div>
        </header>
        <main className="mx-auto max-w-6xl px-4 py-6">{children}</main>
      </body>
    </html>
  );
}
