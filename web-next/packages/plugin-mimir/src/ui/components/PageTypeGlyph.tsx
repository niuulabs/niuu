/**
 * PageTypeGlyph — rune character representing a Mímir page type.
 *
 * Maps each PageType to a canonical Elder Futhark rune from the
 * ShapeSvg / rune glyph map:
 *   entity     → ᚢ (Uruz — the aurochs, primal force)
 *   topic      → ᚦ (Thurisaz — the thorn, directed force)
 *   directive  → ᚱ (Raidho — the ride, forward movement)
 *   preference → ᚷ (Gebo — gift, mutual exchange)
 *   decision   → ᛜ (Ingwaz — the seed, crystallised outcome)
 */

import type { PageType } from '../../domain/page';

const GLYPH_MAP: Record<PageType, string> = {
  entity: 'ᚢ',
  topic: 'ᚦ',
  directive: 'ᚱ',
  preference: 'ᚷ',
  decision: 'ᛜ',
};

interface PageTypeGlyphProps {
  type: PageType;
  size?: number;
  /** Show the type label next to the glyph. */
  showLabel?: boolean;
}

export function PageTypeGlyph({ type, size = 16, showLabel = false }: PageTypeGlyphProps) {
  const glyph = GLYPH_MAP[type];

  return (
    <span
      className="mm-page-type-glyph"
      data-type={type}
      aria-label={type}
      title={type}
      style={{ '--mm-glyph-size': `${size}px` } as React.CSSProperties}
    >
      <span aria-hidden="true">{glyph}</span>
      {showLabel && <span className="mm-page-type-glyph__label">{type}</span>}
    </span>
  );
}
