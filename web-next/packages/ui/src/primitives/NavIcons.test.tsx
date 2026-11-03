import { describe, expect, it } from 'vitest';
import { render } from '@testing-library/react';
import * as icons from './NavIcons';

describe('nav icons', () => {
  it.each(Object.entries(icons))('%s draws on the 32 grid in the current color', (_, Icon) => {
    const { container } = render(<Icon />);
    const svg = container.querySelector('svg')!;
    expect(svg).toHaveAttribute('viewBox', '0 0 32 32');
    expect(svg).toHaveAttribute('fill', 'currentColor');
    expect(svg).toHaveAttribute('aria-hidden', 'true');
    expect(svg.childElementCount).toBeGreaterThan(0);
  });

  it('takes a size and passes other props through', () => {
    const { container } = render(<icons.WorkIcon size={20} className="mark" />);
    const svg = container.querySelector('svg')!;
    expect(svg).toHaveAttribute('width', '20');
    expect(svg).toHaveAttribute('height', '20');
    expect(svg).toHaveClass('mark');
  });
});
