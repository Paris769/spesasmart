import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        // Verde-fiducia (freschezza) come Instacart/Picnic
        primary: {
          DEFAULT: "#16A34A",
          50: "#ECFDF3",
          100: "#D1FADF",
          600: "#16A34A",
          700: "#15803D",
        },
        // Accent caldo "deal": SOLO sconti / risparmio / CTA
        accent: { DEFAULT: "#F97316", 50: "#FFF4ED", 600: "#EA580C" },
        secondary: "#F97316", // alias retro-compat
        deep: "#0B3D2E", // testo headline / superfici premium
        surface: "#FAF8F5", // off-white caldo (lezione Too Good To Go)
        success: "#16A34A",
        warning: "#F59E0B",
        danger: "#DC2626",
        stone: { 200: "#E7E5E4", 500: "#6B7280", 900: "#1A1A1A" },
      },
      borderRadius: { card: "16px", pill: "9999px", btn: "12px" },
      // Scala "money": prezzi trattati come hero (numero grande, tracking stretto)
      fontSize: {
        price: ["30px", { lineHeight: "30px", letterSpacing: "-0.03em", fontWeight: "800" }],
        "price-xl": ["42px", { lineHeight: "42px", letterSpacing: "-0.035em", fontWeight: "800" }],
        hero: ["34px", { lineHeight: "38px", letterSpacing: "-0.03em", fontWeight: "800" }],
      },
      backgroundImage: {
        "hero-grad": "radial-gradient(120% 120% at 0% 0%,#16A34A 0%,#15803D 55%,#0B3D2E 100%)",
        "save-grad": "linear-gradient(135deg,#16A34A 0%,#22C55E 100%)",
        "mesh": "radial-gradient(60% 80% at 90% 0%,rgba(249,115,22,.16),transparent 60%),radial-gradient(70% 90% at 0% 100%,rgba(34,197,94,.16),transparent 55%)",
      },
      boxShadow: {
        card: "0 1px 3px rgba(16,24,40,0.06), 0 1px 2px rgba(16,24,40,0.04)",
        cardHover: "0 8px 24px rgba(16,24,40,0.10)",
        best: "0 6px 24px -6px rgba(22,163,74,0.35), 0 2px 6px rgba(16,24,40,0.06)",
        float: "0 12px 32px -8px rgba(11,61,46,0.22)",
        nav: "0 -1px 16px rgba(16,24,40,0.08)",
      },
      fontFamily: { sans: ["var(--font-inter)", "system-ui", "sans-serif"] },
      keyframes: {
        shimmer: {
          "0%": { backgroundPosition: "-400px 0" },
          "100%": { backgroundPosition: "400px 0" },
        },
        "pop-in": {
          "0%": { transform: "scale(.94)", opacity: "0" },
          "100%": { transform: "scale(1)", opacity: "1" },
        },
        sheen: {
          "0%": { transform: "translateX(-130%) skewX(-12deg)" },
          "100%": { transform: "translateX(260%) skewX(-12deg)" },
        },
      },
      animation: {
        shimmer: "shimmer 1.4s ease infinite",
        "pop-in": "pop-in .28s cubic-bezier(.22,1,.36,1) both",
        sheen: "sheen 1.2s ease .15s",
      },
    },
  },
  plugins: [],
};

export default config;
