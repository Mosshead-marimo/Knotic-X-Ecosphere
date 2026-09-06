import type { Metadata } from "next";
import type { ReactNode } from "react";
import { JetBrains_Mono, Plus_Jakarta_Sans } from "next/font/google";

import "./globals.css";

const sans = Plus_Jakarta_Sans({ subsets: ["latin"], variable: "--font-plus-jakarta", display: "swap" });
const mono = JetBrains_Mono({ subsets: ["latin"], variable: "--font-jetbrains", display: "swap" });

export const metadata: Metadata = {
  title: "VoxSales | AI Operations Console",
  description: "Monitor adaptive AI voice sessions, integration health, qualification, and safe human handoffs.",
};

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="en" className={`${sans.variable} ${mono.variable}`}>
      <body className="min-h-screen bg-ops-base font-sans text-ops-text antialiased">{children}</body>
    </html>
  );
}
