import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import {
  LEFT_DEFAULT_PX,
  LEFT_MAX_PX,
  LEFT_MIN_PX,
  LEFT_WIDTH_KEY,
  useSessionListWidth,
} from './useSessionListWidth';

function Harness({ collapsed = false }: { collapsed?: boolean }) {
  const { width, resizing, separatorProps } = useSessionListWidth(collapsed);
  return (
    <div>
      <span data-testid="width">{width}</span>
      <span data-testid="resizing">{String(resizing)}</span>
      <div {...separatorProps} data-testid="separator" />
    </div>
  );
}

describe('useSessionListWidth', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('starts at the default width and persists it', () => {
    render(<Harness />);
    expect(screen.getByTestId('width')).toHaveTextContent(String(LEFT_DEFAULT_PX));
    expect(localStorage.getItem(LEFT_WIDTH_KEY)).toBe(String(LEFT_DEFAULT_PX));
  });

  it('restores a persisted width and ignores an out-of-range one', () => {
    localStorage.setItem(LEFT_WIDTH_KEY, '420');
    const { unmount } = render(<Harness />);
    expect(screen.getByTestId('width')).toHaveTextContent('420');
    unmount();

    localStorage.setItem(LEFT_WIDTH_KEY, '9000');
    render(<Harness />);
    expect(screen.getByTestId('width')).toHaveTextContent(String(LEFT_DEFAULT_PX));
  });

  it('resizes with the arrow keys and jumps with Home and End', () => {
    render(<Harness />);
    const separator = screen.getByTestId('separator');

    fireEvent.keyDown(separator, { key: 'ArrowRight' });
    expect(screen.getByTestId('width')).toHaveTextContent(String(LEFT_DEFAULT_PX + 20));

    fireEvent.keyDown(separator, { key: 'ArrowLeft' });
    expect(screen.getByTestId('width')).toHaveTextContent(String(LEFT_DEFAULT_PX));

    fireEvent.keyDown(separator, { key: 'End' });
    expect(screen.getByTestId('width')).toHaveTextContent(String(LEFT_MAX_PX));

    fireEvent.keyDown(separator, { key: 'Home' });
    expect(screen.getByTestId('width')).toHaveTextContent(String(LEFT_MIN_PX));

    fireEvent.keyDown(separator, { key: 'a' });
    expect(screen.getByTestId('width')).toHaveTextContent(String(LEFT_MIN_PX));
  });

  it('drags to a new width, clamped to the allowed range', () => {
    render(<Harness />);
    const separator = screen.getByTestId('separator');
    separator.setPointerCapture = vi.fn();

    fireEvent.pointerMove(separator, { clientX: 500 });
    expect(screen.getByTestId('width')).toHaveTextContent(String(LEFT_DEFAULT_PX));

    fireEvent.pointerDown(separator, { clientX: 100, pointerId: 1 });
    expect(screen.getByTestId('resizing')).toHaveTextContent('true');

    fireEvent.pointerMove(separator, { clientX: 150 });
    expect(screen.getByTestId('width')).toHaveTextContent(String(LEFT_DEFAULT_PX + 50));

    fireEvent.pointerMove(separator, { clientX: 5000 });
    expect(screen.getByTestId('width')).toHaveTextContent(String(LEFT_MAX_PX));

    fireEvent.lostPointerCapture(separator);
    expect(screen.getByTestId('resizing')).toHaveTextContent('false');
    expect(localStorage.getItem(LEFT_WIDTH_KEY)).toBe(String(LEFT_MAX_PX));
  });

  it('does not start a drag while the list is collapsed', () => {
    render(<Harness collapsed />);
    const separator = screen.getByTestId('separator');
    separator.setPointerCapture = vi.fn();

    fireEvent.pointerDown(separator, { clientX: 100, pointerId: 1 });
    expect(screen.getByTestId('resizing')).toHaveTextContent('false');
    expect(separator).toHaveAttribute('tabindex', '-1');
  });
});
