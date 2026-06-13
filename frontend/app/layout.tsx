import type { Metadata, Viewport } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import Providers from "./providers";
import BottomNav from "@/components/ui/Navbar";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter" });

const SITE =
  process.env.NEXT_PUBLIC_SITE_URL || "https://spesasmart-seven.vercel.app";

export const metadata: Metadata = {
  metadataBase: new URL(SITE),
  title: "SpesaSmart — Confronta i prezzi della spesa e risparmia",
  description:
    "Confronta i prezzi di migliaia di prodotti nei supermercati vicino a te. Trova dove costa meno e risparmia sulla spesa ogni settimana.",
  applicationName: "SpesaSmart",
  keywords: ["confronto prezzi", "spesa", "supermercati", "risparmio", "offerte"],
  openGraph: {
    title: "SpesaSmart — Confronta i prezzi della spesa",
    description: "Trova dove costa meno la tua spesa tra i supermercati vicino a te.",
    url: SITE,
    siteName: "SpesaSmart",
    locale: "it_IT",
    type: "website",
  },
  appleWebApp: { capable: true, title: "SpesaSmart" },
  icons: { icon: "/icon.svg", apple: "/icon.svg" },
};

export const viewport: Viewport = {
  themeColor: "#16A34A",
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
