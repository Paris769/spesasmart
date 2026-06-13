"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { clsx } from "clsx";
import { Search, ListChecks, MapPin, ScanLine } from "lucide-react";

const links = [
  { href: "/", label: "Cerca", Icon: Search },
  { href: "/lista", label: "Lista", Icon: ListChecks },
  { href: "/mappa", label: "Mappa", Icon: MapPin },
  { href: "/scanner", label: "Scanner", Icon: ScanLine },
];

export default function BottomNav() {
  const path = usePathname();
  return (
    <nav
      className="fixed bottom-0 inset-x-0 z-50 bg-white border-t border-stone-200 shadow-nav"
      style={{ paddingBottom: "env(safe-area-inset-bottom)" }}
    >
      <div className="max-w-5xl mx-auto grid grid-cols-4 h-16">
        {links.map(({ href, label, Icon }) => {
          const active = path === href;
          return (
            <Link
              key={href}
              href={href}
              className={clsx(
                "flex flex-col items-center justify-center gap-0.5 text-[11px] font-medium transition-colors",
                active ? "text-primary" : "text-stone-500"
              )}
            >
              <Icon
                size={22}
                strokeWidth={active ? 2.4 : 1.9}
                className="transition-transform active:scale-90"
              />
              {label}
              <span
                className={clsx(
                  "h-0.5 w-5 rounded-full transition-all",
                  active ? "bg-primary" : "bg-transparent"
                )}
              />
            </Link>
          );
        })}
      </div>
    </nav>
  );
}
