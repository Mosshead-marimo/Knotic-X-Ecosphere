import type { Metadata } from "next";
import type { ReactNode } from "react";
import { Inter } from "next/font/google";

import "./globals.css";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Knotic | Real-Time AI Voice Sales Agent",
  description:
    "Knotic is a real-time AI voice agent that qualifies leads, answers product questions, and books meetings.",
};

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="en" className={inter.variable}>
      <body className="min-h-screen bg-brand-bg font-sans text-black antialiased">{children}</body>
    </html>
  );
}
