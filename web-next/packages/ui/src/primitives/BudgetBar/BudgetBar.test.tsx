import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { BudgetBar } from './BudgetBar';

describe('BudgetBar', () => {
  it('renders with role="meter"', () => {
    render(<BudgetBar spent={50} cap={100} />);
    expect(screen.getByRole('meter')).toBeTruthy();
  });

  it('sets aria-valuenow to percentage rounded', () => {
    render(<BudgetBar spent={25} cap={100} />);
    expect(screen.getByRole('meter')).toHaveAttribute('aria-valuenow', '25');
  });

  it('caps aria-valuenow at 100 when over cap', () => {
    render(<BudgetBar spent={150} cap={100} />);
    // pct is 150 but we clamp fill; aria shows actual pct capped at 100
    const meter = screen.getByRole('meter');
    expect(Number(meter.getAttribute('aria-valuenow'))).toBeLessThanOrEqual(100);
  });

  it('shows ok tone when below warnAt', () => {
    render(<BudgetBar spent={50} cap={100} warnAt={80} />);
    expect(screen.getByRole('meter')).toHaveClass('niuu-budget-bar--ok');
  });

  it('shows warn tone when at or above warnAt', () => {
    render(<BudgetBar spent={80} cap={100} warnAt={80} />);
    expect(screen.getByRole('meter')).toHaveClass('niuu-budget-bar--warn');
  });

  it('shows warn tone when above warnAt', () => {
    render(<BudgetBar spent={90} cap={100} warnAt={80} />);
    expect(screen.getByRole('meter')).toHaveClass('niuu-budget-bar--warn');
  });

  it('shows crit tone when over cap', () => {
    render(<BudgetBar spent={110} cap={100} />);
    expect(screen.getByRole('meter')).toHaveClass('niuu-budget-bar--crit');
  });

  it('shows crit tone when exactly at cap', () => {
    render(<BudgetBar spent={100} cap={100} />);
    expect(screen.getByRole('meter')).toHaveClass('niuu-budget-bar--crit');
  });

  it('does not render label by default', () => {
    render(<BudgetBar spent={50} cap={100} />);
    expect(screen.queryByText('$50.00')).toBeNull();
  });

  it('renders label when showLabel=true', () => {
    render(<BudgetBar spent={50} cap={100} showLabel />);
    expect(screen.getByText('$50.00')).toBeTruthy();
    expect(screen.getByText('$100.00')).toBeTruthy();
  });

  it('handles zero cap gracefully', () => {
    render(<BudgetBar spent={0} cap={0} />);
    const fill = document.querySelector('.niuu-budget-bar__fill') as HTMLElement;
    expect(fill.style.width).toBe('0%');
  });

  it('applies sm size class', () => {
    render(<BudgetBar spent={50} cap={100} size="sm" />);
    expect(screen.getByRole('meter')).toHaveClass('niuu-budget-bar--sm');
  });

  it('applies md size class by default', () => {
    render(<BudgetBar spent={50} cap={100} />);
    expect(screen.getByRole('meter')).toHaveClass('niuu-budget-bar--md');
  });

  it('renders warn threshold marker', () => {
    const { container } = render(<BudgetBar spent={50} cap={100} warnAt={75} />);
    const mark = container.querySelector('.niuu-budget-bar__warn-mark') as HTMLElement;
    expect(mark).toBeTruthy();
    expect(mark.style.left).toBe('75%');
  });

  it('does not render warn marker when warnAt=0', () => {
    const { container } = render(<BudgetBar spent={50} cap={100} warnAt={0} />);
    expect(container.querySelector('.niuu-budget-bar__warn-mark')).toBeNull();
  });

  it('forwards className', () => {
    render(<BudgetBar spent={50} cap={100} className="extra" />);
    expect(screen.getByRole('meter')).toHaveClass('extra');
  });

  it('clamps fill to 100% when over cap', () => {
    const { container } = render(<BudgetBar spent={200} cap={100} />);
    const fill = container.querySelector('.niuu-budget-bar__fill') as HTMLElement;
    expect(fill.style.width).toBe('100%');
  });
});
