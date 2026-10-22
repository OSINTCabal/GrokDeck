import type { Config } from "tailwindcss";

// GrokDeck tokens — mapped onto grok.com's own semantic scale.
// Values live as CSS custom properties in src/index.css; this file only gives
// them Tailwind names. Dark is the default theme.
const config: Config = {
  darkMode: ["class"],
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        base: "rgb(var(--bg-base))",
        sunken: "rgb(var(--bg-sunken))",
        elevated: "rgb(var(--bg-elevated))",
        overlay: "rgb(var(--bg-overlay))",
        inset: "rgb(var(--bg-inset))",
        fg: {
          DEFAULT: "rgb(var(--fg-primary))",
          primary: "rgb(var(--fg-primary))",
          secondary: "rgb(var(--fg-secondary))",
          tertiary: "rgb(var(--fg-tertiary))",
          disabled: "rgb(var(--fg-disabled))",
          accent: "rgb(var(--fg-accent))",
        },
        "fg-accent": "rgb(var(--fg-accent))",
        "accent-soft": "rgb(var(--accent-soft))",
        border: {
          l1: "rgb(var(--border-l1))",
          l2: "rgb(var(--border-l2))",
        },
        "fill-hover": "rgb(var(--fill-hover))",
        "fill-active": "rgb(var(--fill-active))",
        "fill-selected": "rgb(var(--fill-selected))",
        highlight: "rgb(var(--highlight))",
        positive: "rgb(var(--positive))",
        caution: "rgb(var(--caution))",
        critical: "rgb(var(--critical))",
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "-apple-system", "sans-serif"],
        mono: ["Geist Mono", "IBM Plex Mono", "ui-monospace", "SFMono-Regular", "monospace"],
      },
      borderRadius: {
        composer: "var(--radius-composer)",
        card: "var(--radius-card)",
        control: "var(--radius-control)",
        chip: "var(--radius-chip)",
      },
      spacing: {
        sidebar: "var(--sidebar-w)",
        rail: "var(--rail-w)",
        nav: "var(--nav-h)",
      },
      maxWidth: {
        content: "var(--content-max)",
      },
      transitionTimingFunction: {
        out: "var(--ease-out)",
      },
      keyframes: {
        rise: {
          from: { opacity: "0", transform: "translateY(6px)" },
          to: { opacity: "1", transform: "none" },
        },
        pop: {
          from: { opacity: "0", transform: "scale(0.97)" },
          to: { opacity: "1", transform: "none" },
        },
        fade: { from: { opacity: "0" }, to: { opacity: "1" } },
        breathe: {
          "0%,100%": { opacity: "0.25" },
          "50%": { opacity: "1" },
        },
      },
      animation: {
        rise: "rise 260ms var(--ease-out) both",
        pop: "pop 140ms var(--ease-out) both",
        fade: "fade 200ms ease-out both",
        breathe: "breathe 1.4s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};

export default config;