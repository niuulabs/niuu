import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeAll, describe, expect, it } from 'vitest';
import {
  SidebarResizer,
  clampSidebarWidth,
  useResizableSidebar,
  type SidebarBounds,
} from './ResizableSidebar';

const BOUNDS: SidebarBounds = { default: 240, min: 200, max: 400, keyboardStep: 20 };
const KEY = 'niuu.test.sidebarWidth';

function Sidebar() {
  const { width, resizer } = useResizableSidebar(KEY, BOUNDS);
  return (
    <div>
      <aside data-testid="sidebar" style={{ width }} />
      <SidebarResizer label="Resize test sidebar" {...resizer} />
    </div>
  );
}

beforeAll(() => {
  // jsdom has no pointer capture.
  Element.prototype.setPointerCapture = () => {};
});
afterEach(() => localStorage.clear());

describe('clampSidebarWidth', () => {
  it('keeps a width inside its bounds and falls back to the default for nonsense', () => {
    expect(clampSidebarWidth(100, BOUNDS)).toBe(200);
    expect(clampSidebarWidth('900', BOUNDS)).toBe(400);
    expect(clampSidebarWidth('300', BOUNDS)).toBe(300);
    expect(clampSidebarWidth('wide', BOUNDS)).toBe(240);
  });
});

describe('SidebarResizer', () => {
  it('resizes by drag and keeps the width for the next visit', () => {
    const { unmount } = render(<Sidebar />);
    const edge = screen.getByRole('separator', { name: 'Resize test sidebar' });
    expect(edge).toHaveAttribute('aria-valuenow', '240');

    fireEvent.pointerDown(edge, { clientX: 240, pointerId: 1 });
    fireEvent.pointerMove(edge, { clientX: 300, pointerId: 1 });
    expect(screen.getByTestId('sidebar')).toHaveStyle({ width: '300px' });
    fireEvent.pointerUp(edge, { clientX: 320, pointerId: 1 });
    expect(localStorage.getItem(KEY)).toBe('320');

    unmount();
    render(<Sidebar />);
    expect(screen.getByRole('separator')).toHaveAttribute('aria-valuenow', '320');
  });

  it('moves by keyboard, jumps to the ends, and resets on double-click', () => {
    render(<Sidebar />);
    const edge = screen.getByRole('separator');

    fireEvent.keyDown(edge, { key: 'ArrowRight' });
    expect(edge).toHaveAttribute('aria-valuenow', '260');
    fireEvent.keyDown(edge, { key: 'ArrowLeft' });
    expect(edge).toHaveAttribute('aria-valuenow', '240');
    fireEvent.keyDown(edge, { key: 'End' });
    expect(edge).toHaveAttribute('aria-valuenow', '400');
    fireEvent.keyDown(edge, { key: 'Home' });
    expect(edge).toHaveAttribute('aria-valuenow', '200');
    fireEvent.keyDown(edge, { key: 'Enter' });
    expect(edge).toHaveAttribute('aria-valuenow', '200');
    fireEvent.doubleClick(edge);
    expect(edge).toHaveAttribute('aria-valuenow', '240');
  });

  it('drops a cancelled drag', () => {
    render(<Sidebar />);
    const edge = screen.getByRole('separator');
    fireEvent.pointerDown(edge, { clientX: 240, pointerId: 1 });
    fireEvent.pointerMove(edge, { clientX: 380, pointerId: 1 });
    fireEvent.pointerCancel(edge, { pointerId: 1 });
    expect(edge).toHaveAttribute('aria-valuenow', '240');
    expect(localStorage.getItem(KEY)).toBeNull();
  });
});
