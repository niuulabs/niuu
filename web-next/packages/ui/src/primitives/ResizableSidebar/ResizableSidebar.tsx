import { useRef, useState } from 'react';
import { cn } from '../../utils/cn';
import { useStoredPreference } from './useStoredPreference';
import './ResizableSidebar.css';

export interface SidebarBounds {
  default: number;
  min: number;
  max: number;
  /** Pixels one arrow key press moves the edge. */
  keyboardStep: number;
}

/** A stored or dragged width, kept inside the sidebar's bounds. */
export function clampSidebarWidth(value: string | number, bounds: SidebarBounds): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0) return bounds.default;
  return Math.max(bounds.min, Math.min(bounds.max, parsed));
}

export interface SidebarResizerProps {
  label: string;
  width: number;
  bounds: SidebarBounds;
  /** The width while dragging, or null when the drag ends or is cancelled. */
  onDrag: (width: number | null) => void;
  onCommit: (width: number) => void;
  className?: string;
  /**
   * Where the panel sits. A panel on the right grows when its edge moves left, so the drag
   * and the arrow keys run the other way. A panel on the bottom is sized by its top edge:
   * `width` is then its height, and it grows as the edge moves up.
   */
  panelSide?: 'left' | 'right' | 'bottom';
}

/** The arrow key that grows a panel on each side, then the one that shrinks it. */
const ARROW_KEYS = {
  left: ['ArrowRight', 'ArrowLeft'],
  right: ['ArrowLeft', 'ArrowRight'],
  bottom: ['ArrowUp', 'ArrowDown'],
} as const;

/** The draggable edge of a sidebar: drag, arrow keys, Home and End; double-click resets. */
export function SidebarResizer({
  label,
  width,
  bounds,
  onDrag,
  onCommit,
  className,
  panelSide = 'left',
}: SidebarResizerProps) {
  const drag = useRef<{ at: number; width: number } | null>(null);
  const vertical = panelSide === 'bottom';
  const grow = panelSide === 'left' ? 1 : -1;
  const pointerAt = (event: { clientX: number; clientY: number }) =>
    vertical ? event.clientY : event.clientX;
  const widthAt = (at: number) =>
    clampSidebarWidth(drag.current!.width + grow * (at - drag.current!.at), bounds);
  const [growKey, shrinkKey] = ARROW_KEYS[panelSide];
  return (
    <div
      role="separator"
      aria-label={label}
      // A separator's orientation is the line it draws: a bottom panel's edge is horizontal.
      aria-orientation={vertical ? 'horizontal' : 'vertical'}
      aria-valuemin={bounds.min}
      aria-valuemax={bounds.max}
      aria-valuenow={width}
      tabIndex={0}
      className={cn(
        'niuu-sidebar-resizer',
        vertical && 'niuu-sidebar-resizer--horizontal',
        className,
      )}
      onPointerDown={(event) => {
        drag.current = { at: pointerAt(event), width };
        event.currentTarget.setPointerCapture(event.pointerId);
      }}
      onPointerMove={(event) => {
        if (drag.current) onDrag(widthAt(pointerAt(event)));
      }}
      onPointerUp={(event) => {
        if (!drag.current) return;
        onCommit(widthAt(pointerAt(event)));
        drag.current = null;
        onDrag(null);
      }}
      onPointerCancel={() => {
        drag.current = null;
        onDrag(null);
      }}
      onKeyDown={(event) => {
        if (![growKey, shrinkKey, 'Home', 'End'].includes(event.key)) return;
        event.preventDefault();
        const next =
          event.key === 'Home'
            ? bounds.min
            : event.key === 'End'
              ? bounds.max
              : width + (event.key === growKey ? bounds.keyboardStep : -bounds.keyboardStep);
        onCommit(clampSidebarWidth(next, bounds));
      }}
      onDoubleClick={() => onCommit(bounds.default)}
    />
  );
}

/**
 * A sidebar width the person sets by dragging its edge, kept per browser under
 * `storageKey`. Spread `resizer` onto a {@link SidebarResizer} beside the sidebar.
 */
export function useResizableSidebar(storageKey: string, bounds: SidebarBounds) {
  const [stored, setStored] = useStoredPreference<string>(storageKey, String(bounds.default));
  const [dragWidth, setDragWidth] = useState<number | null>(null);
  const width = dragWidth ?? clampSidebarWidth(stored, bounds);
  return {
    width,
    resizer: {
      width,
      bounds,
      onDrag: setDragWidth,
      onCommit: (next: number) => setStored(String(next)),
    },
  } as const;
}
