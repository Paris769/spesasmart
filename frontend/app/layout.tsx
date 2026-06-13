import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import Providers from "./providers";
import BottomNav from "@/components/ui/Navbar";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter" });

export const metadata: Metadata = {
  title: "SpesaSmart — Trova il prezzo migliore vicino a te",
  description:
    "Confronta i prezzi di migliaia di prodotti nei supermercati vicino a te. Risparmia sulla spesa ogni settimana.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="it" className={inter.variable}>
      <body className="min-h-screen">
        <Providers>
          {/* Header snella col brand */}
          <header className="sticky top-0 z-40 bg-surface/80 backdrop-blur border-b border-stone-200">
            <div className="max-w-5xl mx-auto px-4 h-14 flex items-center">
              <span className="font-bold text-lg text-deep tracking-tight">
                🛒 SpesaSmart
              </span>
            </div>
          </header>

          {/* pb-24: lascia spazio alla bottom-nav fissa */}
          <main className="max-w-5xl mx-auto px-4 py-5 pb-24">{children}</main>

          <BottomNav />
        </Providers>
      </body>
    </html>
  );
}
