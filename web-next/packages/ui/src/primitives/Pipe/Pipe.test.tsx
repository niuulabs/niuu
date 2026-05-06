import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Pipe, type PipeCellStatus } from './Pipe';

const cells = [
  { status: 'ok' as PipeCellStatus, label: 'Phase 1' },
  { status: 'run' as PipeCellStatus, label: 'Phase 2' },
  { status: 'warn' as PipeCellStatus },
];

describe('Pipe', () => {
  it('renders one cell per entry', () => {
    render(<Pipe cells={cells} />);
    const items = screen.getAllByRole('listitem');
    expect(items).toHaveLength(3);
  });

  it('applies the correct status class to each cell', () => {
    const { container } = render(<Pipe cells={cells} />);
    const cellEls = container.querySelectorAll('.niuu-pipe__cell');
    expect(cellEls[0]).toHaveClass('niuu-pipe__cell--ok');
    expect(cellEls[1]).toHaveClass('niuu-pipe__cell--run');
    expect(cellEls[2]).toHaveClass('niuu-pipe__cell--warn');
  });

  it('uses label as title when provided', () => {
    render(<Pipe cells={cells} />);
    expect(screen.getAllByRole('listitem')[0]).toHaveAttribute('title', 'Phase 1');
  });

  it('falls back to status as aria-label when no label', () => {
    render(<Pipe cells={cells} />);
    expect(screen.getAllByRole('listitem')[2]).toHaveAttribute('aria-label', 'warn');
  });

  it('applies cell width via inline style', () => {
    const { container } = render(<Pipe cells={[{ status: 'pend' }]} cellWidth={12} />);
    const cell = container.querySelector('.niuu-pipe__cell') as HTMLElement;
    expect(cell.style.width).toBe('12px');
  });

  it('defaults to 18px cell width', () => {
    const { container } = render(<Pipe cells={[{ status: 'pend' }]} />);
    const cell = container.querySelector('.niuu-pipe__cell') as HTMLElement;
    expect(cell.style.width).toBe('18px');
  });

  it.each<PipeCellStatus>(['ok', 'run', 'warn', 'crit', 'gate', 'pend'])(
    'renders status %s',
    (status) => {
      const { container } = render(<Pipe cells={[{ status }]} />);
      expect(container.querySelector(`.niuu-pipe__cell--${status}`)).toBeTruthy();
    },
  );

  it('renders empty list for no cells', () => {
    render(<Pipe cells={[]} />);
    expect(screen.getByRole('list')).toBeInTheDocument();
    expect(screen.queryAllByRole('listitem')).toHaveLength(0);
  });

  it('forwards className', () => {
    render(<Pipe cells={[]} className="extra" />);
    expect(screen.getByRole('list')).toHaveClass('extra');
  });
});
