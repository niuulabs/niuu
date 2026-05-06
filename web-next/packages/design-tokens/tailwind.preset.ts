import type { Config } from 'tailwindcss';

/**
 * Shared Tailwind preset for Niuu packages.
 *
 * Maps every utility back to a CSS custom property declared in
 * `@niuulabs/design-tokens/tokens.css`. Consumers override theme values by
 * swapping `[data-theme]` on the host element — Tailwind classes never change.
 *
 * Usage in each package's `tailwind.config.ts`:
 *
 *     import preset from '@niuulabs/design-tokens/tailwind.preset';
 *     export default { presets: [preset], content: ['src/**\/*.{ts,tsx,css}'] };
 */
const preset = {
  prefix: 'niuu-',
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        'bg-primary': 'var(--color-bg-primary)',
        'bg-secondary': 'var(--color-bg-secondary)',
        'bg-tertiary': 'var(--color-bg-tertiary)',
        'bg-elevated': 'var(--color-bg-elevated)',
        'text-primary': 'var(--color-text-primary)',
        'text-secondary': 'var(--color-text-secondary)',
        'text-muted': 'var(--color-text-muted)',
        'text-faint': 'var(--color-text-faint)',
        border: 'var(--color-border)',
        'border-subtle': 'var(--color-border-subtle)',
        brand: {
          DEFAULT: 'var(--color-brand)',
          100: 'var(--brand-100)',
          200: 'var(--brand-200)',
          300: 'var(--brand-300)',
          400: 'var(--brand-400)',
          500: 'var(--brand-500)',
          600: 'var(--brand-600)',
          700: 'var(--brand-700)',
          800: 'var(--brand-800)',
          900: 'var(--brand-900)',
        },
        critical: {
          DEFAULT: 'var(--color-critical)',
          fg: 'var(--color-critical-fg)',
          bg: 'var(--color-critical-bg)',
          bo: 'var(--color-critical-bo)',
        },
        danger: 'var(--color-danger)',
        gate: {
          DEFAULT: 'var(--color-gate)',
          fg: 'var(--color-gate-fg)',
          bg: 'var(--color-gate-bg)',
          bo: 'var(--color-gate-bo)',
        },
        'state-ok': 'var(--state-ok)',
        'state-ok-bg': 'var(--state-ok-bg)',
        'state-warn': 'var(--state-warn)',
        'state-warn-bg': 'var(--state-warn-bg)',
        'delta-up': 'var(--color-delta-up)',
        'ice-panel': 'var(--ice-panel)',
        'ice-panel-solid': 'var(--ice-panel-solid)',
        'status-emerald': 'var(--status-emerald)',
        'status-green': 'var(--status-green)',
        'status-amber': 'var(--status-amber)',
        'status-orange': 'var(--status-orange)',
        'status-red': 'var(--status-red)',
        'status-cyan': 'var(--status-cyan)',
        'status-indigo': 'var(--status-indigo)',
        'status-purple': 'var(--status-purple)',
      },
      fontFamily: {
        sans: 'var(--font-sans)',
        mono: 'var(--font-mono)',
      },
      fontSize: {
        xs: 'var(--text-xs)',
        sm: 'var(--text-sm)',
        base: 'var(--text-base)',
        lg: 'var(--text-lg)',
        xl: 'var(--text-xl)',
        '2xl': 'var(--text-2xl)',
        '3xl': 'var(--text-3xl)',
        '4xl': 'var(--text-4xl)',
      },
      spacing: {
        0: 'var(--space-0)',
        1: 'var(--space-1)',
        2: 'var(--space-2)',
        3: 'var(--space-3)',
        4: 'var(--space-4)',
        5: 'var(--space-5)',
        6: 'var(--space-6)',
        8: 'var(--space-8)',
        10: 'var(--space-10)',
        12: 'var(--space-12)',
      },
      borderRadius: {
        sm: 'var(--radius-sm)',
        md: 'var(--radius-md)',
        lg: 'var(--radius-lg)',
        xl: 'var(--radius-xl)',
        '2xl': 'var(--radius-2xl)',
        full: 'var(--radius-full)',
      },
      boxShadow: {
        sm: 'var(--shadow-sm)',
        md: 'var(--shadow-md)',
        lg: 'var(--shadow-lg)',
        'ice-glow': 'var(--ice-glow)',
      },
      transitionDuration: {
        fast: '150ms',
        normal: '200ms',
        slow: '300ms',
      },
    },
  },
  corePlugins: {
    // tokens.css already sets `box-sizing: border-box` globally via the reset
    // shipped alongside the app; disable Tailwind's preflight to avoid fighting it.
    preflight: false,
  },
} satisfies Partial<Config>;

export default preset;
