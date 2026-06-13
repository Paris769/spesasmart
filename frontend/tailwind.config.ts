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
      boxShadow: {
        card: "0 1px 3px rgba(16,24,40,0.06), 0 1px 2px rgba(16,24,40,0.04)",
        cardHover: "0 8px 24px rgba(16,24,40,0.10)",
        nav: "0 -1px 16px rgba(16,24,40,0.08)",
      },
      fontFamily: { sans: ["var(--font-inter)", "system-ui", "sans-serif"] },
      keyframes: {
        shimmer: {
          "0%": { backgroundPosition: "-400px 0" },
          "100%": { backgroundPosition: "400px 0" },
        },
      },
      animation: { shimmer: "shimmer 1.4s ease infinite" },
    },
  },
  plugins: [],
};

export default config;
