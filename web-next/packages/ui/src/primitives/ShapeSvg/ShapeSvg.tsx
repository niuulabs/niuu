import type { SVGProps } from 'react';

/**
 * The marks the Observatory canvas draws, as flat SVG previews.
 *
 * Kept in step with `entityShapeSchema` in `@niuulabs/domain` and with the
 * canvas' own glyph table: a registry editor that offers a shape the canvas
 * cannot draw is offering the operator a way to break their own graph.
 */
export type ShapeKind =
  | 'ring'
  | 'ring-dashed'
  | 'cloud'
  | 'agent'
  | 'halo'
  | 'triangle'
  | 'box'
  | 'hex'
  | 'pentagon'
  | 'square-sm'
  | 'rack'
  | 'printer'
  | 'hex-flat'
  | 'cylinder'
  | 'beacon'
  | 'mimir';

/** Color tokens accepted by ShapeSvg — maps to design-token CSS variables */
export type ShapeColor =
  | 'brand'
  | 'brand-100'
  | 'brand-200'
  | 'brand-300'
  | 'brand-400'
  | 'brand-500'
  | 'ice-100'
  | 'ice-200'
  | 'ice-300'
  | 'slate-300'
  | 'slate-400';

export interface ShapeSvgProps {
  shape: ShapeKind;
  color?: ShapeColor;
  size?: number;
  className?: string;
  'aria-label'?: string;
}

const COLOR_VARS: Record<ShapeColor, string> = {
  brand: 'var(--color-brand)',
  'brand-100': 'var(--brand-100)',
  'brand-200': 'var(--brand-200)',
  'brand-300': 'var(--brand-300)',
  'brand-400': 'var(--brand-400)',
  'brand-500': 'var(--brand-500)',
  'ice-100': 'var(--brand-100)',
  'ice-200': 'var(--brand-200)',
  'ice-300': 'var(--brand-300)',
  'slate-300': 'var(--color-text-secondary)',
  'slate-400': 'var(--color-text-muted)',
};

function resolveColor(color: ShapeColor | undefined): string {
  if (!color) return 'var(--color-brand)';
  return COLOR_VARS[color];
}

export function ShapeSvg({
  shape,
  color,
  size = 20,
  className,
  'aria-label': ariaLabel,
}: ShapeSvgProps) {
  const c = resolveColor(color);
  const svgProps: SVGProps<SVGSVGElement> = {
    width: size,
    height: size,
    viewBox: '-10 -10 20 20',
    xmlns: 'http://www.w3.org/2000/svg',
    role: 'img',
    className,
    'aria-label': ariaLabel ?? shape,
  };

  switch (shape) {
    case 'ring':
      return (
        <svg {...svgProps}>
          <circle cx="0" cy="0" r="7" fill="none" stroke={c} strokeWidth="1.4" />
        </svg>
      );

    case 'ring-dashed':
      return (
        <svg {...svgProps}>
          <circle
            cx="0"
            cy="0"
            r="7"
            fill="none"
            stroke={c}
            strokeWidth="1.2"
            strokeDasharray="2 2"
          />
        </svg>
      );

    case 'rack':
      return (
        <svg {...svgProps}>
          <rect
            x="-7"
            y="-5"
            width="14"
            height="10"
            rx="2"
            fill="none"
            stroke={c}
            strokeWidth="1.4"
          />
          <path d="M-5,-2 H5 M-5,1 H5" stroke={c} strokeWidth="1" opacity={0.55} />
        </svg>
      );

    case 'agent':
      return (
        <svg {...svgProps}>
          <circle
            cx="0"
            cy="0"
            r="8"
            fill="none"
            stroke={c}
            strokeWidth="1"
            strokeDasharray="3 3"
            opacity={0.6}
          />
          <circle cx="0" cy="0" r="5" fill="var(--color-bg-primary)" stroke={c} strokeWidth="1.6" />
          <circle cx="0" cy="0" r="1.8" fill={c} />
        </svg>
      );

    case 'triangle':
      return (
        <svg {...svgProps}>
          <path d="M0,-7 L6,5 L-6,5 Z" fill={c} />
        </svg>
      );

    case 'hex':
      return (
        <svg {...svgProps}>
          <path
            d="M-6,-3.5 L0,-7 L6,-3.5 L6,3.5 L0,7 L-6,3.5 Z"
            fill="none"
            stroke={c}
            strokeWidth="1.4"
          />
        </svg>
      );

    case 'hex-flat':
      return (
        <svg {...svgProps}>
          <path
            d="M-3.5,-6 L3.5,-6 L7,0 L3.5,6 L-3.5,6 L-7,0 Z"
            fill="none"
            stroke={c}
            strokeWidth="1.4"
          />
        </svg>
      );

    case 'cylinder':
      return (
        <svg {...svgProps}>
          <rect
            x="-5"
            y="-6"
            width="10"
            height="12"
            rx="3"
            fill="none"
            stroke={c}
            strokeWidth="1.4"
          />
          <ellipse cx="0" cy="-3" rx="5" ry="1.9" fill="none" stroke={c} strokeWidth="1.2" />
        </svg>
      );

    case 'cloud':
      return (
        <svg {...svgProps}>
          {/* Lobes as separate circles: at swatch size the union reads as a
              cloud without needing the canvas's silhouette trace. */}
          <g fill="none" stroke={c} strokeWidth="1.2" strokeDasharray="2 2">
            <circle cx="-4.5" cy="1.5" r="3.4" />
            <circle cx="-1" cy="-1.8" r="4.2" />
            <circle cx="3" cy="0" r="3.6" />
            <circle cx="0.5" cy="2.8" r="3.2" />
          </g>
        </svg>
      );

    case 'printer':
      return (
        <svg {...svgProps}>
          <rect
            x="-7"
            y="-1"
            width="14"
            height="7"
            rx="1.5"
            fill="none"
            stroke={c}
            strokeWidth="1.4"
          />
          <line x1="-5" y1="3" x2="5" y2="3" stroke={c} strokeWidth="1" opacity={0.5} />
          <line x1="-4.5" y1="-4.5" x2="4.5" y2="-4.5" stroke={c} strokeWidth="1.6" />
          <line x1="0" y1="-4.5" x2="0" y2="-8" stroke={c} strokeWidth="1.2" />
        </svg>
      );

    case 'beacon':
      return (
        <svg {...svgProps}>
          <circle cx="0" cy="0" r="8" fill="none" stroke={c} strokeWidth="1" opacity={0.4} />
          <circle cx="0" cy="0" r="3.5" fill={c} />
        </svg>
      );

    case 'square-sm':
      return (
        <svg {...svgProps}>
          <rect x="-5" y="-5" width="10" height="10" fill="none" stroke={c} strokeWidth="1.4" />
        </svg>
      );

    case 'pentagon':
      return (
        <svg {...svgProps}>
          <path d="M0,-7 L6.6,-2.2 L4.1,5.6 L-4.1,5.6 L-6.6,-2.2 Z" fill={c} />
        </svg>
      );

    case 'halo':
      return (
        <svg {...svgProps}>
          <circle
            cx="0"
            cy="0"
            r="7"
            fill="none"
            stroke={c}
            strokeWidth="1"
            strokeDasharray="1 2"
          />
          <circle cx="0" cy="0" r="2.5" fill={c} />
        </svg>
      );

    case 'mimir':
      return (
        <svg {...svgProps}>
          <circle cx="0" cy="0" r="5" fill="var(--color-bg-primary)" stroke={c} strokeWidth="1.4" />
          <text
            x="0"
            y="1"
            fontSize="5"
            fill={c}
            textAnchor="middle"
            dominantBaseline="middle"
            fontFamily="monospace"
          >
            ᛗ
          </text>
        </svg>
      );

    case 'box':
    default:
      return (
        <svg {...svgProps}>
          <rect
            x="-6"
            y="-6"
            width="12"
            height="12"
            rx="2"
            fill="none"
            stroke={c}
            strokeWidth="1.4"
          />
          <circle cx="0" cy="0" r="2" fill={c} />
        </svg>
      );
  }
}
