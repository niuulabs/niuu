import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ConfidenceBar, type ConfidenceLevel } from './ConfidenceBar';

describe('ConfidenceBar', () => {
  it.each<ConfidenceLevel>(['high', 'medium', 'low'])('renders level %s', (level) => {
    render(<ConfidenceBar level={level} />);
    const el = screen.getByRole('meter');
    expect(el).toHaveTextContent(level);
    expect(el).toHaveClass(`niuu-conf-bar--${level}`);
  });

  it('has accessible aria attributes for high', () => {
    render(<ConfidenceBar level="high" />);
    const el = screen.getByRole('meter');
    expect(el).toHaveAttribute('aria-valuenow', '100');
    expect(el).toHaveAttribute('aria-valuemin', '0');
    expect(el).toHaveAttribute('aria-valuemax', '100');
    expect(el).toHaveAttribute('aria-label', 'confidence: high');
  });

  it('has aria-valuenow 60 for medium', () => {
    render(<ConfidenceBar level="medium" />);
    expect(screen.getByRole('meter')).toHaveAttribute('aria-valuenow', '60');
  });

  it('has aria-valuenow 25 for low', () => {
    render(<ConfidenceBar level="low" />);
    expect(screen.getByRole('meter')).toHaveAttribute('aria-valuenow', '25');
  });

  it('renders track and fill elements', () => {
    const { container } = render(<ConfidenceBar level="high" />);
    expect(container.querySelector('.niuu-conf-bar__track')).toBeTruthy();
    expect(container.querySelector('.niuu-conf-bar__fill')).toBeTruthy();
  });

  it('forwards className', () => {
    render(<ConfidenceBar level="medium" className="extra" />);
    expect(screen.getByRole('meter')).toHaveClass('extra');
  });
});
