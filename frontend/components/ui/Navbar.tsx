"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { clsx } from "clsx";

const links = [
  { href: "/",        label: "Cerca" },
  { href: "/lista",   label: "Lista spesa" },
  { href: "/mappa",   label: "Mappa negozi" },
  { href: "/scanner", label: "Scanner" },
];

export default function Navbar() {
  const path = usePathname();
  return (
    <nav className="bg-primary shadow-sm sticky top-0 z-50">
      <div className="max-w-5xl mx-auto px-4 flex items-center justify-between h-14">
        <Link href="/" className="text-white font-bold text-xl tracking-tight">
          🛒 SpesaSmart
        </Link>
        <div className="flex gap-1">
          {links.map((l) => (
            <Link
              key={l.href}
              href={l.href}
              className={clsx(
                "px-3 py-1.5 rounded text-sm font-medium transition-colors",
                path === l.href
                  ? "bg-white text-primary"
                  : "text-white hover:bg-green-700"
              )}
            >
              {l.label}
            </Link>
          ))}
        </div>
      </div>
    </nav>
  );
}
