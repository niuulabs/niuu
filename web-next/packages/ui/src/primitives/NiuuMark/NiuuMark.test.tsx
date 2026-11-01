import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { NiuuMark, NiuuWordmark } from './NiuuMark';

describe('NiuuMark', () => {
  it('is decorative unless it is given a title', () => {
    const { container } = render(<NiuuMark />);
    expect(container.querySelector('svg')).toHaveAttribute('aria-hidden', 'true');
    expect(screen.queryByRole('img')).not.toBeInTheDocument();
  });

  it('exposes its title to assistive tech', () => {
    render(<NiuuMark title="NIUU" />);
    expect(screen.getByRole('img', { name: 'NIUU' })).toBeInTheDocument();
  });

  it('uses the small master strokes at UI sizes', () => {
    const { container } = render(<NiuuMark size={22} className="extra" />);
    const svg = container.querySelector('svg');
    expect(svg).toHaveClass('extra');
    expect(svg).toHaveAttribute('width', '22');
    const [back, front] = Array.from(container.querySelectorAll('path'));
    expect(back).toHaveAttribute('stroke-width', '7');
    expect(front).toHaveAttribute('stroke-width', '9.2');
  });

  it('uses the flat master strokes at larger sizes', () => {
    const { container } = render(<NiuuMark size={96} />);
    const [back, front] = Array.from(container.querySelectorAll('path'));
    expect(back).toHaveAttribute('stroke-width', '3.35');
    expect(front).toHaveAttribute('stroke-width', '5.1');
  });

  it('reads its colours from the mark tokens', () => {
    const { container } = render(<NiuuMark />);
    const [back, front] = Array.from(container.querySelectorAll('path'));
    expect(back).toHaveAttribute('stroke', 'var(--color-mark-back)');
    expect(front).toHaveAttribute('stroke', 'var(--color-mark-front)');
  });
});

describe('NiuuWordmark', () => {
  it('draws the four letters as paths at the requested height', () => {
    const { container } = render(<NiuuWordmark height={30} className="extra" />);
    const svg = container.querySelector('svg');
    expect(svg).toHaveAttribute('height', '30');
    expect(svg).toHaveClass('extra');
    expect(container.querySelectorAll('path')).toHaveLength(4);
  });
});
