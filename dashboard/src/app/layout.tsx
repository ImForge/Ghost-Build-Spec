import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";

import { Nav } from "@/components/Nav";
import { LiveProvider } from "@/components/LiveProvider";
import { PROJECT_NAME } from "@/lib/constants";
import "./globals.css";

const inter = Inter({ variable: "--font-inter", subsets: ["latin"] });
const mono = JetBrains_Mono({ variable: "--font-mono-face", subsets: ["latin"] });

export const metadata: Metadata = {
  title: `${PROJECT_NAME} — campus safety`,
  description:
    "Privacy-first edge-AI campus safety. Skeletons at the edge, alerts on a digital twin.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${inter.variable} ${mono.variable} h-full`}>
      {/* A fixed-height flex column: the twin and the alert table scroll
          inside their own panels, so the page itself never does. Without the
          hard height, flex-1 resolves against content and the floor plan grows
          until it pushes everything off screen. */}
      <body className="flex h-full flex-col overflow-hidden bg-bg text-ink">
        <LiveProvider>
          <Nav />
          <main className="min-h-0 flex-1 overflow-hidden">{children}</main>
        </LiveProvider>
      </body>
    </html>
  );
}
