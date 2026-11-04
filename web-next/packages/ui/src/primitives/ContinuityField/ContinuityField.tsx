import { cn } from '../../utils/cn';
import {
  BRANCHES,
  COMPANION_LINES,
  MAIN_PATH,
  TRANSITIONS,
  VIEW_BOX,
} from './continuityFieldPaths';
import './ContinuityField.css';

/* Ids are global to the document; a page shows one field, so fixed ids are enough. */
const PATH_GRADIENT_ID = 'niuu-continuity-path';
const HAZE_GRADIENT_ID = 'niuu-continuity-haze';
const FADE_GRADIENT_ID = 'niuu-continuity-fade';
const FADE_MASK_ID = 'niuu-continuity-mask';
const GLOW_FILTER_ID = 'niuu-continuity-glow';

export type ContinuityFieldComposition = 'hero' | 'centre';

export interface ContinuityFieldProps {
  /**
   * `hero` clears the left for a claim on wide screens. `centre` clears the
   * middle for a centred column. Both are centred on narrow screens.
   */
  composition?: ContinuityFieldComposition;
  className?: string;
}

/**
 * The NIUU continuity field: one bright continuous path with quiet companion
 * lines, the brand's background for hero surfaces. It fills the viewport behind
 * its stacking context, so the parent must create one. Static, per the brand.
 */
export function ContinuityField({ composition = 'centre', className }: ContinuityFieldProps) {
  return (
    <div
      aria-hidden="true"
      data-testid="continuity-field"
      className={cn(
        'niuu-continuity',
        composition === 'hero' && 'niuu-continuity--hero',
        className,
      )}
    >
      <svg
        className="niuu-continuity__wave"
        viewBox={VIEW_BOX}
        preserveAspectRatio="xMidYMid slice"
        xmlns="http://www.w3.org/2000/svg"
      >
        <defs>
          <linearGradient id={PATH_GRADIENT_ID} x1="0" x2="1">
            <stop stopColor="var(--_field-edge)" stopOpacity="0" />
            <stop offset=".35" stopColor="var(--_field-edge)" stopOpacity=".52" />
            <stop offset=".72" stopColor="var(--_field-path)" stopOpacity=".9" />
            <stop offset="1" stopColor="var(--_field-line)" stopOpacity=".45" />
          </linearGradient>
          <radialGradient id={HAZE_GRADIENT_ID} cx="70%" cy="49%" r="50%">
            <stop stopColor="var(--_field-haze)" stopOpacity=".25" />
            <stop offset="1" stopColor="var(--_field-haze)" stopOpacity="0" />
          </radialGradient>
          <linearGradient id={FADE_GRADIENT_ID}>
            <stop stopColor="white" stopOpacity="0" />
            <stop offset=".21" stopColor="white" stopOpacity=".08" />
            <stop offset=".45" stopColor="white" stopOpacity=".7" />
            <stop offset=".82" stopColor="white" />
            <stop offset="1" stopColor="white" stopOpacity=".65" />
          </linearGradient>
          <mask id={FADE_MASK_ID}>
            <rect width="100%" height="100%" fill={`url(#${FADE_GRADIENT_ID})`} />
          </mask>
          <filter id={GLOW_FILTER_ID}>
            <feGaussianBlur stdDeviation="13" />
          </filter>
        </defs>
        <rect width="100%" height="100%" fill={`url(#${HAZE_GRADIENT_ID})`} />
        <g fill="none" mask={`url(#${FADE_MASK_ID})`}>
          {COMPANION_LINES.map((line) => (
            <path
              key={line.d}
              d={line.d}
              stroke="var(--_field-line)"
              strokeWidth={line.width}
              opacity={line.opacity}
            />
          ))}
          {BRANCHES.map((branch) => (
            <g key={branch.d}>
              <path d={branch.d} stroke="var(--_field-line)" strokeWidth={1.1} opacity={0.34} />
              <circle
                cx={branch.cx}
                cy={branch.cy}
                r={3}
                fill="var(--_field-line)"
                opacity={0.55}
              />
            </g>
          ))}
          <path
            d={MAIN_PATH}
            stroke="var(--_field-line)"
            strokeWidth={23}
            opacity={0.15}
            filter={`url(#${GLOW_FILTER_ID})`}
          />
          <path
            d={MAIN_PATH}
            stroke={`url(#${PATH_GRADIENT_ID})`}
            strokeWidth={2.3}
            opacity={0.91}
          />
          <path d={MAIN_PATH} stroke="var(--_field-path)" strokeWidth={0.65} opacity={0.8} />
          {TRANSITIONS.map((point) => (
            <g key={point.cx}>
              <circle cx={point.cx} cy={point.cy} r={4} fill="var(--_field-path)" opacity={0.82} />
              <circle
                cx={point.cx}
                cy={point.cy}
                r={10}
                stroke="var(--_field-line)"
                strokeWidth={0.7}
                opacity={0.3}
              />
            </g>
          ))}
        </g>
      </svg>
    </div>
  );
}
