import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react';

// Compact UX: configurable (drag-resizable) left session column, persisted.
export const LEFT_WIDTH_KEY = 'niuu.compactUx.sessions.leftWidth';
export const LEFT_MIN_PX = 200;
export const LEFT_MAX_PX = 560;
export const LEFT_DEFAULT_PX = 300;
const KEY_STEP_PX = 20;

export function readLeftWidth(): number {
  if (typeof window === 'undefined') return LEFT_DEFAULT_PX;
  try {
    const v = Number(window.localStorage.getItem(LEFT_WIDTH_KEY));
    return Number.isFinite(v) && v >= LEFT_MIN_PX && v <= LEFT_MAX_PX ? v : LEFT_DEFAULT_PX;
  } catch {
    return LEFT_DEFAULT_PX;
  }
}

function clampWidth(width: number): number {
  return Math.max(LEFT_MIN_PX, Math.min(LEFT_MAX_PX, width));
}

/**
 * The persisted width of the session list column plus the props for the drag
 * handle that changes it. Shared by the Advanced and Simple session pages so
 * both remember the same column width.
 */
export function useSessionListWidth(collapsed = false) {
  const [width, setWidth] = useState<number>(readLeftWidth);
  const [resizing, setResizing] = useState(false);
  const resizeOrigin = useRef<{ x: number; width: number } | null>(null);

  useEffect(() => {
    try {
      window.localStorage.setItem(LEFT_WIDTH_KEY, String(width));
    } catch {
      /* localStorage unavailable — non-fatal */
    }
  }, [width]);

  const separatorProps = {
    role: 'separator',
    'aria-orientation': 'vertical' as const,
    'aria-label': 'Resize session list',
    tabIndex: collapsed ? -1 : 0,
    'aria-valuemin': LEFT_MIN_PX,
    'aria-valuemax': LEFT_MAX_PX,
    'aria-valuenow': width,
    className: 'vol-session-resize',
    onKeyDown: (event: KeyboardEvent<HTMLDivElement>) => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      setWidth((current) =>
        event.key === 'Home'
          ? LEFT_MIN_PX
          : event.key === 'End'
            ? LEFT_MAX_PX
            : clampWidth(current + (event.key === 'ArrowRight' ? KEY_STEP_PX : -KEY_STEP_PX)),
      );
    },
    onPointerDown: (event: PointerEvent<HTMLDivElement>) => {
      if (collapsed) return;
      resizeOrigin.current = { x: event.clientX, width };
      event.currentTarget.setPointerCapture(event.pointerId);
      setResizing(true);
    },
    onPointerMove: (event: PointerEvent<HTMLDivElement>) => {
      const origin = resizeOrigin.current;
      if (!origin) return;
      setWidth(clampWidth(origin.width + event.clientX - origin.x));
    },
    onLostPointerCapture: () => {
      resizeOrigin.current = null;
      setResizing(false);
    },
  };

  return { width, resizing, separatorProps };
}
