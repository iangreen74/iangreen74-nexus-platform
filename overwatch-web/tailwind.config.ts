import type { Config } from 'tailwindcss';

// Operator-specific design tokens.
// DELIBERATELY DIFFERENT from Forgewing v6.1 (navy/forge-orange/parchment).
// Engineering-flavored: high contrast, terminal-adjacent, no warmth.
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        op: {
          bg: '#0F1115',
          surface: '#1A1D24',
          'surface-2': '#22262F',
          border: '#2D323D',
          text: '#E4E7EC',
          'text-dim': '#9BA1AB',
          'text-muted': '#6B7280',
          accent: '#5EEAD4',
          'accent-dim': '#2DD4BF',
          warning: '#FBBF24',
          danger: '#F87171',
          success: '#86EFAC',
        },
      },
      fontFamily: {
        mono: ['JetBrains Mono', 'Fira Code', 'Consolas', 'Menlo', 'monospace'],
        sans: ['Inter', 'system-ui', 'sans-serif'],
      },
      fontSize: {
        '2xs': ['0.6875rem', '1rem'],
      },
    },
  },
  plugins: [],
} satisfies Config;
