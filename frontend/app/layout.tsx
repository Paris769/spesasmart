import type { Metadata } from "next";
import "./globals.css";
import Providers from "./providers";
import Navbar from "@/components/ui/Navbar";

export const metadata: Metadata = {
  title: "SpesaSmart — Trova il prezzo migliore vicino a te",
  description:
    "Confronta i prezzi di migliaia di prodotti nei supermercati vicino a te. Risparmia sulla spesa ogni settimana.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="it">
      <body className="bg-gray-50 min-h-screen">
        <Providers>
          <Navbar />
          <main className="max-w-5xl mx-auto px-4 py-6">{children}</main>
        </Providers>
      </body>
    </html>
  );
}
