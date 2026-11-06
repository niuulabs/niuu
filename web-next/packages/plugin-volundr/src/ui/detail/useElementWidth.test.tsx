import { describe, expect, it, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import { useElementHeight, useElementWidth } from './useElementWidth';

describe('useElementWidth', () => {
  it('follows the observed width, forgets it for another element and stops on unmount', () => {
    let notify: ResizeObserverCallback = () => {};
    const disconnect = vi.fn();
    vi.stubGlobal(
      'ResizeObserver',
      class {
        constructor(callback: ResizeObserverCallback) {
          notify = callback;
        }
        observe = vi.fn();
        disconnect = disconnect;
      },
    );
    const report = (width: number) =>
      act(() => notify([{ contentRect: { width } } as ResizeObserverEntry], {} as ResizeObserver));

    const { result, rerender, unmount } = renderHook(({ el }) => useElementWidth(el), {
      initialProps: { el: null as HTMLElement | null },
    });
    expect(result.current).toBeNull();

    rerender({ el: document.createElement('div') });
    expect(result.current).toBeNull();
    report(1200);
    expect(result.current).toBe(1200);
    report(900);
    expect(result.current).toBe(900);

    rerender({ el: document.createElement('section') });
    expect(result.current).toBeNull();

    unmount();
    expect(disconnect).toHaveBeenCalled();
    vi.unstubAllGlobals();
  });

  it('follows the observed height and ignores reports that carry none', () => {
    let notify: ResizeObserverCallback = () => {};
    vi.stubGlobal(
      'ResizeObserver',
      class {
        constructor(callback: ResizeObserverCallback) {
          notify = callback;
        }
        observe = vi.fn();
        disconnect = vi.fn();
      },
    );
    const report = (rect: Partial<DOMRectReadOnly>) =>
      act(() => notify([{ contentRect: rect } as ResizeObserverEntry], {} as ResizeObserver));
    const element = document.createElement('div');

    const { result } = renderHook(() => useElementHeight(element));
    expect(result.current).toBeNull();
    report({ height: 640 });
    expect(result.current).toBe(640);
    report({ width: 1200 });
    expect(result.current).toBe(640);
    vi.unstubAllGlobals();
  });
});
