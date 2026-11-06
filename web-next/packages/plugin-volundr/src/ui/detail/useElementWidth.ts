import { useEffect, useState } from 'react';

type Dimension = 'width' | 'height';

function useElementDimension(element: HTMLElement | null, dimension: Dimension): number | null {
  const [measured, setMeasured] = useState<{ element: HTMLElement; size: number } | null>(null);
  useEffect(() => {
    if (!element) return;
    // A new observation reports the element's current size first, then every change.
    const observer = new ResizeObserver(([entry]) => {
      const size = entry?.contentRect[dimension];
      if (typeof size === 'number') setMeasured({ element, size });
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [element, dimension]);
  return measured && measured.element === element ? measured.size : null;
}

/** The element's rendered width in pixels, kept current as it resizes; null until measured. */
export function useElementWidth(element: HTMLElement | null): number | null {
  return useElementDimension(element, 'width');
}

/** The element's rendered height in pixels, kept current as it resizes; null until measured. */
export function useElementHeight(element: HTMLElement | null): number | null {
  return useElementDimension(element, 'height');
}
