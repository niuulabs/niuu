import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ShapeSvg, type ShapeKind, type ShapeColor } from './ShapeSvg';
import { ENTITY_RUNES, SERVICE_RUNES, RUNE_MAP } from './runeMap';

const FORBIDDEN_RUNES = new Set(['ᛟ', 'ᛊ', 'ᛏ', 'ᛉ', 'ᚺ', 'ᚻ']);

const ALL_SHAPES: ShapeKind[] = [
  'ring',
  'ring-dashed',
  'rounded-rect',
  'diamond',
  'triangle',
  'hex',
  'chevron',
  'square',
  'square-sm',
  'pentagon',
  'halo',
  'mimir',
  'mimir-small',
  'dot',
];

describe('ShapeSvg', () => {
  describe('renders valid SVG for every shape', () => {
    it.each(ALL_SHAPES)('%s', (shape) => {
      render(<ShapeSvg shape={shape} aria-label={shape} />);
      const svg = screen.getByRole('img', { name: shape });
      expect(svg.tagName.toLowerCase()).toBe('svg');
      expect(svg).toHaveAttribute('viewBox', '-10 -10 20 20');
    });
  });

  it('defaults to size 20', () => {
    render(<ShapeSvg shape="dot" />);
    const svg = screen.getByRole('img');
    expect(svg).toHaveAttribute('width', '20');
    expect(svg).toHaveAttribute('height', '20');
  });

  it('applies custom size', () => {
    render(<ShapeSvg shape="ring" size={36} aria-label="ring" />);
    const svg = screen.getByRole('img');
    expect(svg).toHaveAttribute('width', '36');
    expect(svg).toHaveAttribute('height', '36');
  });

  it('uses brand color when no color prop given', () => {
    const { container } = render(<ShapeSvg shape="ring" />);
    expect(container.innerHTML).toContain('var(--color-brand)');
  });

  it('applies explicit brand color', () => {
    const { container } = render(<ShapeSvg shape="dot" color="brand" />);
    expect(container.innerHTML).toContain('var(--color-brand)');
  });

  it.each([
    ['ice-100', 'var(--brand-100)'],
    ['ice-200', 'var(--brand-200)'],
    ['ice-300', 'var(--brand-300)'],
    ['brand-100', 'var(--brand-100)'],
    ['brand-200', 'var(--brand-200)'],
    ['brand-300', 'var(--brand-300)'],
    ['brand-400', 'var(--brand-400)'],
    ['brand-500', 'var(--brand-500)'],
    ['slate-300', 'var(--color-text-secondary)'],
    ['slate-400', 'var(--color-text-muted)'],
  ] satisfies [ShapeColor, string][])('color "%s" resolves to "%s"', (colorProp, expectedVar) => {
    const { container } = render(<ShapeSvg shape="dot" color={colorProp} />);
    expect(container.innerHTML).toContain(expectedVar);
  });

  it('uses shape as default aria-label', () => {
    render(<ShapeSvg shape="pentagon" />);
    expect(screen.getByRole('img', { name: 'pentagon' })).toBeInTheDocument();
  });

  it('accepts a custom aria-label', () => {
    render(<ShapeSvg shape="hex" aria-label="hexagonal node" />);
    expect(screen.getByRole('img', { name: 'hexagonal node' })).toBeInTheDocument();
  });

  it('forwards className to the svg element', () => {
    render(<ShapeSvg shape="dot" className="test-class" />);
    expect(screen.getByRole('img')).toHaveClass('test-class');
  });

  it('mimir and mimir-small render the ᛗ glyph', () => {
    const { container } = render(<ShapeSvg shape="mimir" />);
    expect(container.textContent).toContain('ᛗ');
  });

  it('mimir-small renders the same SVG structure as mimir', () => {
    const { container: a } = render(<ShapeSvg shape="mimir" />);
    const { container: b } = render(<ShapeSvg shape="mimir-small" />);
    // Both shapes share the same SVG output (circle + rune text)
    expect(a.querySelector('circle')).toBeTruthy();
    expect(b.querySelector('circle')).toBeTruthy();
    expect(a.querySelector('text')).toBeTruthy();
    expect(b.querySelector('text')).toBeTruthy();
  });

  it('halo renders two concentric circles', () => {
    const { container } = render(<ShapeSvg shape="halo" />);
    const circles = container.querySelectorAll('circle');
    expect(circles).toHaveLength(2);
  });
});

describe('ENTITY_RUNES', () => {
  it('contains no forbidden runes', () => {
    for (const [key, rune] of Object.entries(ENTITY_RUNES)) {
      expect(
        FORBIDDEN_RUNES.has(rune),
        `ENTITY_RUNES["${key}"] = "${rune}" is a forbidden rune`,
      ).toBe(false);
    }
  });

  it('has entries for all core entity kinds', () => {
    const expected = [
      'realm',
      'cluster',
      'host',
      'ravn_long',
      'ravn_raid',
      'skuld',
      'valkyrie',
      'tyr',
      'bifrost',
      'volundr',
      'mimir',
      'mimir_sub',
      'service',
      'model',
      'printer',
      'vaettir',
      'beacon',
      'raid',
    ] as const;
    for (const kind of expected) {
      expect(ENTITY_RUNES).toHaveProperty(kind);
    }
  });
});

describe('SERVICE_RUNES', () => {
  it('contains no forbidden runes', () => {
    for (const [key, rune] of Object.entries(SERVICE_RUNES)) {
      expect(
        FORBIDDEN_RUNES.has(rune),
        `SERVICE_RUNES["${key}"] = "${rune}" is a forbidden rune`,
      ).toBe(false);
    }
  });

  it('maps canonical system names to their glyphs', () => {
    expect(SERVICE_RUNES.volundr).toBe('ᚲ');
    expect(SERVICE_RUNES.tyr).toBe('ᛃ');
    expect(SERVICE_RUNES.ravn).toBe('ᚱ');
    expect(SERVICE_RUNES.mimir).toBe('ᛗ');
    expect(SERVICE_RUNES.bifrost).toBe('ᚨ');
    expect(SERVICE_RUNES.sleipnir).toBe('ᛖ');
    expect(SERVICE_RUNES.buri).toBe('ᛜ');
    expect(SERVICE_RUNES.hlidskjalf).toBe('ᛞ');
    expect(SERVICE_RUNES.flokk).toBe('ᚠ');
    expect(SERVICE_RUNES.skuld).toBe('ᚾ');
    expect(SERVICE_RUNES.valkyrie).toBe('ᛒ');
  });
});

describe('RUNE_MAP', () => {
  it('contains no forbidden runes', () => {
    for (const [key, rune] of Object.entries(RUNE_MAP)) {
      expect(FORBIDDEN_RUNES.has(rune), `RUNE_MAP["${key}"] = "${rune}" is a forbidden rune`).toBe(
        false,
      );
    }
  });

  it('includes keys from both SERVICE_RUNES and ENTITY_RUNES', () => {
    for (const key of Object.keys(SERVICE_RUNES)) {
      expect(RUNE_MAP).toHaveProperty(key);
    }
    for (const key of Object.keys(ENTITY_RUNES)) {
      expect(RUNE_MAP).toHaveProperty(key);
    }
  });
});
