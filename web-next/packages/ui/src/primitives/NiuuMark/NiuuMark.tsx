/*
 * The NIUU orbit mark and wordmark. Path data comes from the brand masters in
 * docs/design/brand/assets; the strokes read `--color-mark-*` so a light
 * surface gets the dark variant without a second component.
 */
const ORBIT_BACK_PATH =
  'M95 191C98 146 108 91 135 58C158 30 183 41 194 69C207 101 197 140 168 170C148 190 123 197 100 187';
const ORBIT_FRONT_PATH =
  'M58 91C83 80 120 102 162 125C186 139 209 150 212 134C215 119 193 106 164 96C123 82 84 83 58 91C39 98 43 119 54 143C67 173 92 190 121 191C158 192 183 166 199 133C205 118 205 105 202 101';

/** Below this size the flat master's strokes fall under a pixel; use the small master's. */
const SMALL_MARK_MAX_SIZE = 47;
const SMALL_STROKES = { back: 7, front: 9.2 };
const FLAT_STROKES = { back: 3.35, front: 5.1 };

export interface NiuuMarkProps {
  size?: number;
  className?: string;
  /** Names the mark for assistive tech. Omit when it is decorative. */
  title?: string;
}

export function NiuuMark({ size = 28, className, title }: NiuuMarkProps) {
  const strokes = size <= SMALL_MARK_MAX_SIZE ? SMALL_STROKES : FLAT_STROKES;
  return (
    <svg
      className={className}
      viewBox="0 0 256 256"
      width={size}
      height={size}
      fill="none"
      strokeLinecap="round"
      strokeLinejoin="round"
      role={title ? 'img' : undefined}
      aria-label={title}
      aria-hidden={title ? undefined : true}
    >
      <path d={ORBIT_BACK_PATH} stroke="var(--color-mark-back)" strokeWidth={strokes.back} />
      <path d={ORBIT_FRONT_PATH} stroke="var(--color-mark-front)" strokeWidth={strokes.front} />
    </svg>
  );
}

export interface NiuuWordmarkProps {
  height?: number;
  className?: string;
}

/** Path-drawn lettering: the wordmark is never set in a font. */
export function NiuuWordmark({ height = 24, className }: NiuuWordmarkProps) {
  return (
    <svg
      className={className}
      viewBox="0 0 354 72"
      height={height}
      fill="none"
      stroke="var(--color-text-primary)"
      strokeWidth={5.1}
      strokeLinecap="square"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M4 63V6L51 63V6" />
      <path d="M112 6V63" />
      <path d="M176 6V45Q176 63 193 63H215Q232 63 232 45V6" />
      <path d="M293 6V45Q293 63 310 63H332Q349 63 349 45V6" />
    </svg>
  );
}
