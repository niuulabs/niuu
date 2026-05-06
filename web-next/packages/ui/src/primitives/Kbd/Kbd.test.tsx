import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Kbd } from './Kbd';

describe('Kbd', () => {
  it('renders a kbd element with content', () => {
    render(<Kbd>⌘K</Kbd>);
    const el = screen.getByText('⌘K');
    expect(el.tagName).toBe('KBD');
    expect(el).toHaveClass('niuu-kbd');
  });

  it('merges className', () => {
    render(<Kbd className="extra">x</Kbd>);
    expect(screen.getByText('x')).toHaveClass('niuu-kbd', 'extra');
  });
});
