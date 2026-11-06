import { useRef, useState } from 'react';
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
   * and the arrow keys run the other way.
   */
  panelSide?: 'left' | 'right';
}

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
  const drag = useRef<{ x: number; width: number } | null>(null);
  const grow = panelSide === 'left' ? 1 : -1;
  const widthAt = (clientX: number) =>
    clampSidebarWidth(drag.current!.width + grow * (clientX - drag.current!.x), bounds);
  return (
    <div
      role="separator"
      aria-label={label}
      aria-orientation="vertical"
      aria-valuemin={bounds.min}
      aria-valuemax={bounds.max}
      aria-valuenow={width}
      tabIndex={0}
      className={className ? `niuu-sidebar-resizer ${className}` : 'niuu-sidebar-resizer'}
      onPointerDown={(event) => {
        drag.current = { x: event.clientX, width };
        event.currentTarget.setPointerCapture(event.pointerId);
      }}
      onPointerMove={(event) => {
        if (drag.current) onDrag(widthAt(event.clientX));
      }}
      onPointerUp={(event) => {
        if (!drag.current) return;
        onCommit(widthAt(event.clientX));
        drag.current = null;
        onDrag(null);
      }}
      onPointerCancel={() => {
        drag.current = null;
        onDrag(null);
      }}
      onKeyDown={(event) => {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault();
        const next =
          event.key === 'Home'
            ? bounds.min
            : event.key === 'End'
              ? bounds.max
              : width +
                grow * (event.key === 'ArrowRight' ? bounds.keyboardStep : -bounds.keyboardStep);
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
