import './Dockable.css';
import { useEffect, useRef, type PointerEvent, type ReactNode, type RefObject } from 'react';
import { createPortal } from 'react-dom';
import { Maximize2, X } from 'lucide-react';

/**
 * A column that can float over the page: popped out it is a window the person drags by its
 * title and resizes from its corner; closing it puts it back in its column.
 */
export function Dockable({
  label,
  floating,
  onFloatingChange,
  className,
  panelRef,
  actions,
  children,
}: {
  label: string;
  floating: boolean;
  onFloatingChange: (floating: boolean) => void;
  className: string;
  /** The column itself, docked or floating, for a page that looks inside it. */
  panelRef?: RefObject<HTMLElement | null>;
  /** The column's own controls, beside the one that pops it out. */
  actions?: ReactNode;
  children: ReactNode;
}) {
  const own = useRef<HTMLElement>(null);
  const panel = panelRef ?? own;
  const drag = useRef<{ x: number; y: number } | null>(null);

  useEffect(() => {
    if (!floating) return;
    function dock(event: KeyboardEvent) {
      if (event.key === 'Escape') onFloatingChange(false);
    }
    window.addEventListener('keydown', dock);
    return () => window.removeEventListener('keydown', dock);
  }, [floating, onFloatingChange]);

  function startDrag(event: PointerEvent<HTMLDivElement>) {
    const element = panel.current;
    if (!element || (event.target as HTMLElement).closest('button')) return;
    const box = element.getBoundingClientRect();
    drag.current = { x: event.clientX - box.left, y: event.clientY - box.top };
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function moveDrag(event: PointerEvent<HTMLDivElement>) {
    const element = panel.current;
    if (!element || !drag.current) return;
    const box = element.getBoundingClientRect();
    // Kept inside the window, so the title to drag it back by never leaves the screen.
    const left = Math.min(Math.max(event.clientX - drag.current.x, 0), innerWidth - box.width);
    const top = Math.min(Math.max(event.clientY - drag.current.y, 0), innerHeight - box.height);
    element.style.left = `${left}px`;
    element.style.top = `${top}px`;
    element.style.right = 'auto';
  }

  const head = (
    <div
      className="ting-dockable__head"
      onPointerDown={floating ? startDrag : undefined}
      onPointerMove={floating ? moveDrag : undefined}
      onPointerUp={() => {
        drag.current = null;
      }}
    >
      <span>{label}</span>
      {actions}
      {floating ? (
        <button
          type="button"
          aria-label="Put back in its column"
          onClick={() => onFloatingChange(false)}
        >
          <X aria-hidden="true" />
        </button>
      ) : (
        <button type="button" aria-label="Pop out" onClick={() => onFloatingChange(true)}>
          <Maximize2 aria-hidden="true" />
        </button>
      )}
    </div>
  );

  if (!floating) {
    return (
      <aside ref={panel} className={className} aria-label={label}>
        {head}
        {children}
      </aside>
    );
  }
  return createPortal(
    <aside
      ref={panel}
      className={`${className} ting-dockable--floating`}
      aria-label={label}
      role="dialog"
    >
      {head}
      {children}
    </aside>,
    document.body,
  );
}
