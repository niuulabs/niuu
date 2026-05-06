import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ConfidenceBadge } from './ConfidenceBadge';

describe('ConfidenceBadge', () => {
  describe('null/zero rendering', () => {
    it('renders — for null value', () => {
      const { container } = render(<ConfidenceBadge value={null} />);
      expect(container.querySelector('.niuu-conf-badge__num')).toHaveTextContent('—');
    });

    it('renders — for value 0', () => {
      const { container } = render(<ConfidenceBadge value={0} />);
      expect(container.querySelector('.niuu-conf-badge__num')).toHaveTextContent('—');
    });

    it('adds null modifier class for null', () => {
      const { container } = render(<ConfidenceBadge value={null} />);
      expect(container.querySelector('.niuu-conf-badge')).toHaveClass('niuu-conf-badge--null');
    });

    it('adds null modifier class for 0', () => {
      const { container } = render(<ConfidenceBadge value={0} />);
      expect(container.querySelector('.niuu-conf-badge')).toHaveClass('niuu-conf-badge--null');
    });

    it('does not render a meter role for null', () => {
      render(<ConfidenceBadge value={null} />);
      expect(screen.queryByRole('meter')).toBeNull();
    });
  });

  describe('numeric rendering', () => {
    it('shows the percentage for a normal value', () => {
      render(<ConfidenceBadge value={0.75} />);
      expect(screen.getByRole('meter')).toHaveTextContent('75');
    });

    it('rounds to nearest integer', () => {
      render(<ConfidenceBadge value={0.876} />);
      expect(screen.getByRole('meter')).toHaveTextContent('88');
    });

    it('has correct aria attributes', () => {
      render(<ConfidenceBadge value={0.85} />);
      const el = screen.getByRole('meter');
      expect(el).toHaveAttribute('aria-valuenow', '85');
      expect(el).toHaveAttribute('aria-valuemin', '0');
      expect(el).toHaveAttribute('aria-valuemax', '100');
      expect(el).toHaveAttribute('aria-label', 'confidence: 85%');
    });
  });

  describe('tier mapping', () => {
    it('applies hi tier for value >= 0.80', () => {
      render(<ConfidenceBadge value={0.9} />);
      expect(screen.getByRole('meter')).toHaveClass('niuu-conf-badge--hi');
    });

    it('applies hi tier exactly at 0.80', () => {
      render(<ConfidenceBadge value={0.8} />);
      expect(screen.getByRole('meter')).toHaveClass('niuu-conf-badge--hi');
    });

    it('applies md tier for value >= 0.50 and < 0.80', () => {
      render(<ConfidenceBadge value={0.65} />);
      expect(screen.getByRole('meter')).toHaveClass('niuu-conf-badge--md');
    });

    it('applies md tier exactly at 0.50', () => {
      render(<ConfidenceBadge value={0.5} />);
      expect(screen.getByRole('meter')).toHaveClass('niuu-conf-badge--md');
    });

    it('applies lo tier for value < 0.50', () => {
      render(<ConfidenceBadge value={0.3} />);
      expect(screen.getByRole('meter')).toHaveClass('niuu-conf-badge--lo');
    });
  });

  it('renders track and fill for non-zero value', () => {
    const { container } = render(<ConfidenceBadge value={0.7} />);
    expect(container.querySelector('.niuu-conf-badge__track')).toBeTruthy();
    expect(container.querySelector('.niuu-conf-badge__fill')).toBeTruthy();
  });

  it('forwards className', () => {
    render(<ConfidenceBadge value={0.8} className="extra" />);
    expect(screen.getByRole('meter')).toHaveClass('extra');
  });
});
