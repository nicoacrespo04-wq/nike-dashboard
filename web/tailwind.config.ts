import type { Config } from 'tailwindcss'

/**
 * Nike Analytics — design tokens.
 *
 * Everything here is additive: no existing token was renamed or removed, so
 * every class already used by the pages keeps working. New tokens exist so the
 * pages can stop hardcoding hex values (`bg-[#0046CC]`, `text-[#111111]`, ...).
 */
const config: Config = {
  content: [
    './src/pages/**/*.{js,ts,jsx,tsx,mdx}',
    './src/components/**/*.{js,ts,jsx,tsx,mdx}',
    './src/app/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      colors: {
        // ── Nike house palette (pre-existing keys kept verbatim) ──────────
        nike: {
          black:       '#111111',
          red:         '#E31837',
          white:       '#FFFFFF',
          gray:        '#F5F5F5',
          'dark-gray': '#757575',
          'mid-gray':  '#CCCCCC',
          // additions
          'light-gray': '#E5E5E5',
          ink:          '#111111',
          'ink-soft':   '#3D3D3D',
          muted:        '#757575',
          faint:        '#9B9B9B',
        },

        // ── Competitor brands — single source of truth ────────────────────
        brand: {
          nike:   '#E31837',
          black:  '#111111',
          adidas: '#0046CC',
          puma:   '#E4032E',
        },

        /**
         * BML status palette. Semantics (do not invert):
         *   BEAT = Nike más barato  → verde
         *   MEET = precio similar   → naranja
         *   LOSE = Nike más caro    → rojo
         */
        bml: {
          beat: '#27AE60',
          meet: '#F5A623',
          lose: '#E31837',
          nd:   '#9B9B9B',
          // soft surfaces for badges
          'beat-soft': '#E7F6ED',
          'meet-soft': '#FEF3E0',
          'lose-soft': '#FCE8EC',
          'nd-soft':   '#F2F2F2',
          'beat-ink':  '#1B7A43',
          'meet-ink':  '#9A6206',
          'lose-ink':  '#A81028',
          'nd-ink':    '#6B6B6B',
        },

        // ── Surfaces / chrome ─────────────────────────────────────────────
        surface: {
          DEFAULT: '#FFFFFF',
          muted:   '#FAFAFA',
          page:    '#F5F5F5',
          sunken:  '#F0F0F0',
          border:  '#EDEDED',
          'border-strong': '#DCDCDC',
        },
      },

      fontFamily: {
        sans: ['Inter', 'Helvetica Neue', 'Arial', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },

      /**
       * Numeric hierarchy for the dashboard. `metric-*` are the "big number"
       * sizes (tight tracking, tabular figures applied via `.tabnum`),
       * `label` / `micro` are the recessive supporting sizes.
       */
      fontSize: {
        micro:      ['0.625rem', { lineHeight: '0.875rem', letterSpacing: '0.06em' }],
        label:      ['0.6875rem', { lineHeight: '1rem', letterSpacing: '0.08em' }],
        'metric-sm': ['1.5rem',  { lineHeight: '1.05', letterSpacing: '-0.02em' }],
        'metric-md': ['1.875rem', { lineHeight: '1.05', letterSpacing: '-0.025em' }],
        'metric-lg': ['2.25rem', { lineHeight: '1', letterSpacing: '-0.03em' }],
        'metric-xl': ['3rem',    { lineHeight: '1', letterSpacing: '-0.035em' }],
      },

      spacing: {
        card: '1.25rem',   // nike-card padding
        gutter: '1rem',    // grid gap between cards
        section: '1.5rem', // vertical rhythm between sections
      },

      borderRadius: {
        card: '0.75rem',
        pill: '9999px',
      },

      boxShadow: {
        card:       '0 1px 2px 0 rgba(17,17,17,0.04), 0 1px 3px 0 rgba(17,17,17,0.04)',
        'card-hover': '0 4px 12px -2px rgba(17,17,17,0.10), 0 2px 4px -2px rgba(17,17,17,0.06)',
        popover:    '0 8px 24px -6px rgba(17,17,17,0.18), 0 2px 6px -2px rgba(17,17,17,0.08)',
        'sticky-header': '0 1px 0 0 rgba(17,17,17,0.08)',
      },

      keyframes: {
        shimmer: {
          '0%':   { backgroundPosition: '-200% 0' },
          '100%': { backgroundPosition: '200% 0' },
        },
        'fade-in': {
          from: { opacity: '0', transform: 'translateY(2px)' },
          to:   { opacity: '1', transform: 'translateY(0)' },
        },
      },

      animation: {
        shimmer: 'shimmer 1.6s linear infinite',
        'fade-in': 'fade-in 140ms ease-out',
      },

      transitionDuration: {
        fast: '120ms',
      },
    },
  },
  plugins: [],
}

export default config
