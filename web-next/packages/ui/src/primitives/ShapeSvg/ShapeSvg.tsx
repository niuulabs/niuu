import type { SVGProps } from 'react';

export type ShapeKind =
  | 'ring'
  | 'ring-dashed'
  | 'rounded-rect'
  | 'diamond'
  | 'triangle'
  | 'hex'
  | 'chevron'
  | 'square'
  | 'square-sm'
  | 'pentagon'
  | 'halo'
  | 'mimir'
  | 'mimir-small'
  | 'dot';

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

    case 'rounded-rect':
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
        </svg>
      );

    case 'diamond':
      return (
        <svg {...svgProps}>
          <path d="M0,-7 L7,0 L0,7 L-7,0 Z" fill={c} opacity={0.85} />
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

    case 'chevron':
      return (
        <svg {...svgProps}>
          <path d="M-6,5 L0,-6 L6,5 L0,2 Z" fill={c} />
        </svg>
      );

    case 'square':
      return (
        <svg {...svgProps}>
          <rect x="-6" y="-6" width="12" height="12" rx="1" fill={c} />
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
    case 'mimir-small':
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

    case 'dot':
    default:
      return (
        <svg {...svgProps}>
          <circle cx="0" cy="0" r="4" fill={c} />
        </svg>
      );
  }
}
