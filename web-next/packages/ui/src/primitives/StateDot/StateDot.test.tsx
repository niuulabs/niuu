import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { StateDot } from './StateDot';

describe('StateDot', () => {
  it('applies the state modifier class', () => {
    render(<StateDot state="failed" title="died" />);
    const dot = screen.getByRole('status');
    expect(dot).toHaveClass('niuu-state-dot--failed');
    expect(dot).toHaveAttribute('title', 'died');
  });

  it('adds pulse class when pulse=true', () => {
    render(<StateDot state="running" pulse />);
    expect(screen.getByRole('status')).toHaveClass('niuu-state-dot--pulse');
  });
});
