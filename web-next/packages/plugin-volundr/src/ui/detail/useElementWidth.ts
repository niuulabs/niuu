import { useEffect, useState } from 'react';

/** The element's rendered width in pixels, kept current as it resizes; null until measured. */
export function useElementWidth(element: HTMLElement | null): number | null {
  const [measured, setMeasured] = useState<{ element: HTMLElement; width: number } | null>(null);
  useEffect(() => {
    if (!element) return;
    // A new observation reports the element's current size first, then every change.
    const observer = new ResizeObserver(([entry]) => {
      if (entry) setMeasured({ element, width: entry.contentRect.width });
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [element]);
  return measured && measured.element === element ? measured.width : null;
}
