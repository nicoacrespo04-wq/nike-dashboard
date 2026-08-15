import type { Config } from "tailwindcss";

/**
 * Branding Nike sobrio: rojo de marca, negro casi puro, fondo claro.
 * Los colores de dato (factores, severidades) viven en src/lib/viz.ts —
 * acá sólo la identidad de la aplicación.
 */
const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        nike: {
          red: "#E31837",
          "red-dark": "#B81229",
          ink: "#111111",
        },
        surface: {
          page: "#F6F6F4",
          card: "#FFFFFF",
          sunken: "#FAFAF8",
        },
        line: {
          DEFAULT: "#E3E3DF",
          strong: "#C9C9C3",
        },
        ink: {
          primary: "#111111",
          secondary: "#5A5A55",
          muted: "#8A8A83",
        },
      },
      fontFamily: {
        sans: ["var(--font-sans)", "system-ui", "-apple-system", "Segoe UI", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      fontSize: {
        "2xs": ["0.6875rem", { lineHeight: "1rem" }],
      },
      boxShadow: {
        card: "0 1px 2px rgba(17,17,17,0.05), 0 1px 3px rgba(17,17,17,0.04)",
        pop: "0 8px 24px rgba(17,17,17,0.10)",
      },
    },
  },
  plugins: [],
};

export default config;
