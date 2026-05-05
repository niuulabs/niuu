import { describe, it, expect } from 'vitest';
import { readFileSync } from 'fs';
import { resolve } from 'path';

const css = readFileSync(resolve(__dirname, 'tokens.css'), 'utf-8');

/** Extract all `--foo:` definitions from the :root block. */
function definedTokens(): Set<string> {
  const tokens = new Set<string>();
  for (const m of css.matchAll(/--[\w-]+(?=\s*:)/g)) {
    tokens.add(m[0]);
  }
  return tokens;
}

describe('tokens.css completeness', () => {
  const tokens = definedTokens();

  it('defines core background tokens', () => {
    for (const t of [
      '--color-bg-primary',
      '--color-bg-secondary',
      '--color-bg-tertiary',
      '--color-bg-elevated',
    ]) {
      expect(tokens.has(t), `missing ${t}`).toBe(true);
    }
  });

  it('defines text color tokens including --color-text-faint', () => {
    for (const t of [
      '--color-text-primary',
      '--color-text-secondary',
      '--color-text-muted',
      '--color-text-faint',
    ]) {
      expect(tokens.has(t), `missing ${t}`).toBe(true);
    }
  });

  it('defines the full brand ramp (100–900) plus --color-brand', () => {
    expect(tokens.has('--color-brand'), 'missing --color-brand').toBe(true);
    for (const n of [100, 200, 300, 400, 500, 600, 700, 800, 900]) {
      expect(tokens.has(`--brand-${n}`), `missing --brand-${n}`).toBe(true);
    }
  });

  it('defines color-brand-N aliases for web2 compat', () => {
    for (const n of [100, 200, 300, 400, 500, 600, 700, 800, 900]) {
      expect(tokens.has(`--color-brand-${n}`), `missing --color-brand-${n}`).toBe(true);
    }
  });

  it('defines --color-danger as alias for --color-critical', () => {
    expect(tokens.has('--color-danger'), 'missing --color-danger').toBe(true);
    expect(tokens.has('--color-critical'), 'missing --color-critical').toBe(true);
  });

  it('defines ice utility tokens (panel, panel-solid, glow)', () => {
    for (const t of ['--ice-panel', '--ice-panel-solid', '--ice-glow']) {
      expect(tokens.has(t), `missing ${t}`).toBe(true);
    }
  });

  it('defines direct-hue status tokens for Mimir', () => {
    for (const t of [
      '--status-emerald',
      '--status-green',
      '--status-amber',
      '--status-orange',
      '--status-red',
      '--status-cyan',
      '--status-purple',
    ]) {
      expect(tokens.has(t), `missing ${t}`).toBe(true);
    }
  });

  it('defines semantic status tokens', () => {
    for (const t of [
      '--status-healthy',
      '--status-running',
      '--status-observing',
      '--status-merged',
      '--status-attention',
      '--status-review',
      '--status-queued',
      '--status-processing',
      '--status-deciding',
      '--status-failed',
      '--status-degraded',
      '--status-unknown',
      '--status-idle',
      '--status-archived',
      '--status-gated',
    ]) {
      expect(tokens.has(t), `missing ${t}`).toBe(true);
    }
  });

  it('defines gate tokens', () => {
    for (const t of ['--color-gate', '--color-gate-fg', '--color-gate-bg', '--color-gate-bo']) {
      expect(tokens.has(t), `missing ${t}`).toBe(true);
    }
  });

  it('defines critical tokens', () => {
    for (const t of [
      '--color-critical',
      '--color-critical-fg',
      '--color-critical-bg',
      '--color-critical-bo',
    ]) {
      expect(tokens.has(t), `missing ${t}`).toBe(true);
    }
  });

  it('defines spacing, radius, shadow, and motion tokens', () => {
    for (const n of [0, 1, 2, 3, 4, 5, 6, 8, 10, 12]) {
      expect(tokens.has(`--space-${n}`), `missing --space-${n}`).toBe(true);
    }
    for (const r of ['sm', 'md', 'lg', 'xl', '2xl', 'full']) {
      expect(tokens.has(`--radius-${r}`), `missing --radius-${r}`).toBe(true);
    }
    for (const s of ['sm', 'md', 'lg']) {
      expect(tokens.has(`--shadow-${s}`), `missing --shadow-${s}`).toBe(true);
    }
    for (const t of ['fast', 'normal', 'slow']) {
      expect(tokens.has(`--transition-${t}`), `missing --transition-${t}`).toBe(true);
    }
  });

  it('ice theme overrides exist in [data-theme="ice"]', () => {
    expect(css).toContain("[data-theme='ice']");
  });

  it('amber theme overrides exist in [data-theme="amber"]', () => {
    expect(css).toContain("[data-theme='amber']");
  });

  it('spring theme overrides exist in [data-theme="spring"]', () => {
    expect(css).toContain("[data-theme='spring']");
  });
});
