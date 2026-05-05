# Web UI Styling Rules

## Scope

These rules apply to the **legacy `web/`** app only. Styling in **`web-next/`** follows
its own convention (Tailwind CSS + design tokens) — see `web-next/CLAUDE.md` rule 6.

If you are working in `web-next/`, stop reading this file and use Tailwind. If you are
working in `web/`, the rules below apply.

---

## Forbidden Practices (in `web/`)

The following are **strictly forbidden** in `web/`:

### No Inline Styles

```tsx
// FORBIDDEN
<div style={{ backgroundColor: '#18181b' }}>
<div style={someStyleObject}>
```

### No Tailwind/Utility Classes in JSX (legacy only)

```tsx
// FORBIDDEN in web/
<div className="bg-zinc-900 p-4 rounded-lg">
<div className="flex items-center gap-2">
```

> In `web-next/` Tailwind is the default — this prohibition does not apply there.

### No CSS-in-JS Libraries

Do not use styled-components, emotion, or similar runtime CSS solutions.

## Required Practices (in `web/`)

### CSS Modules for Component Styles

Every component that needs styling must have a corresponding `.module.css` file:

```
components/
  StatusBadge/
    StatusBadge.tsx
    StatusBadge.module.css  # Required
    index.ts
```

Usage:
```tsx
import styles from './StatusBadge.module.css';

export function StatusBadge({ status }: Props) {
  return (
    <span className={styles.badge} data-status={status}>
      {status}
    </span>
  );
}
```

### Design Tokens via CSS Custom Properties

All colors, spacing, typography must use tokens from `styles/tokens.css`:

```css
/* StatusBadge.module.css */
.badge {
  padding: var(--space-1) var(--space-2);
  border-radius: var(--radius-full);
  font-size: var(--text-xs);
  font-family: var(--font-sans);
}

.badge[data-status="healthy"] {
  background-color: color-mix(in srgb, var(--color-accent-emerald) 20%, transparent);
  color: var(--color-accent-emerald);
  border: 1px solid color-mix(in srgb, var(--color-accent-emerald) 30%, transparent);
}
```

### Composing Classes

Use the `cn()` utility for conditional classes:

```tsx
import { cn } from '@/utils/classnames';
import styles from './Button.module.css';

export function Button({ variant, disabled }: Props) {
  return (
    <button
      className={cn(
        styles.button,
        styles[variant],
        disabled && styles.disabled
      )}
    />
  );
}
```

## File Organization

```
src/
  styles/
    tokens.css      # CSS custom properties (colors, spacing, etc.)
    reset.css       # CSS reset/normalize
    typography.css  # Font definitions and text styles
    utilities.css   # Shared utility classes (sr-only, etc.)

  components/
    ComponentName/
      ComponentName.tsx
      ComponentName.module.css
      index.ts
```

## Token Categories

### Colors

```css
/* Background colors */
--color-bg-primary: #09090b;    /* zinc-950 */
--color-bg-secondary: #18181b;  /* zinc-900 */
--color-bg-tertiary: #27272a;   /* zinc-800 */
--color-bg-elevated: #3f3f46;   /* zinc-700 */

/* Text colors */
--color-text-primary: #fafafa;  /* zinc-50 */
--color-text-secondary: #a1a1aa; /* zinc-400 */
--color-text-muted: #71717a;    /* zinc-500 */

/* Border colors */
--color-border: #3f3f46;        /* zinc-700 */
--color-border-subtle: #27272a; /* zinc-800 */

/* Accent colors - semantic */
--color-accent-amber: #f59e0b;
--color-accent-cyan: #06b6d4;
--color-accent-emerald: #10b981;
--color-accent-purple: #a855f7;
--color-accent-red: #ef4444;
--color-accent-indigo: #6366f1;
--color-accent-orange: #f97316;
```

### Spacing

```css
--space-0: 0;
--space-1: 0.25rem;   /* 4px */
--space-2: 0.5rem;    /* 8px */
--space-3: 0.75rem;   /* 12px */
--space-4: 1rem;      /* 16px */
--space-5: 1.25rem;   /* 20px */
--space-6: 1.5rem;    /* 24px */
--space-8: 2rem;      /* 32px */
--space-10: 2.5rem;   /* 40px */
--space-12: 3rem;     /* 48px */
```

### Border Radius

```css
--radius-sm: 0.375rem;  /* 6px */
--radius-md: 0.5rem;    /* 8px */
--radius-lg: 0.75rem;   /* 12px */
--radius-xl: 1rem;      /* 16px */
--radius-2xl: 1.5rem;   /* 24px */
--radius-full: 9999px;
```

### Typography

```css
--font-sans: 'Inter', system-ui, sans-serif;
--font-mono: 'JetBrains Mono', monospace;

--text-xs: 0.75rem;     /* 12px */
--text-sm: 0.875rem;    /* 14px */
--text-base: 1rem;      /* 16px */
--text-lg: 1.125rem;    /* 18px */
--text-xl: 1.25rem;     /* 20px */
--text-2xl: 1.5rem;     /* 24px */
```

## Why These Rules?

1. **Performance**: CSS Modules are compiled at build time, no runtime overhead
2. **Maintainability**: Styles are co-located with components
3. **Consistency**: Design tokens enforce visual consistency
4. **Refactoring**: Easy to find and update styles
5. **Type safety**: Module class names are statically analyzed
6. **No conflicts**: CSS Modules scope classes automatically
