import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { BudgetRunwayBar } from './BudgetRunwayBar';

describe('BudgetRunwayBar', () => {
  it('renders with role="meter"', () => {
    render(<BudgetRunwayBar spent={40} projected={80} cap={100} elapsedFrac={0.5} />);
    expect(screen.getByRole('meter')).toBeTruthy();
  });

  it('has aria-label "budget runway"', () => {
    render(<BudgetRunwayBar spent={40} projected={80} cap={100} elapsedFrac={0.5} />);
    expect(screen.getByRole('meter')).toHaveAttribute('aria-label', 'budget runway');
  });

  it('sets aria-valuenow to spent percentage', () => {
    render(<BudgetRunwayBar spent={40} projected={80} cap={100} elapsedFrac={0.5} />);
    expect(screen.getByRole('meter')).toHaveAttribute('aria-valuenow', '40');
  });

  it('renders spent, projected, and now-mark elements', () => {
    const { container } = render(
      <BudgetRunwayBar spent={30} projected={70} cap={100} elapsedFrac={0.4} />,
    );
    expect(container.querySelector('.niuu-budget-runway__spent')).toBeTruthy();
    expect(container.querySelector('.niuu-budget-runway__proj')).toBeTruthy();
    expect(container.querySelector('.niuu-budget-runway__now-mark')).toBeTruthy();
  });

  it('positions spent fill correctly', () => {
    const { container } = render(
      <BudgetRunwayBar spent={25} projected={60} cap={100} elapsedFrac={0.3} />,
    );
    const spent = container.querySelector('.niuu-budget-runway__spent') as HTMLElement;
    expect(spent.style.width).toBe('25%');
  });

  it('positions projected fill relative to spent', () => {
    const { container } = render(
      <BudgetRunwayBar spent={25} projected={75} cap={100} elapsedFrac={0.3} />,
    );
    const proj = container.querySelector('.niuu-budget-runway__proj') as HTMLElement;
    expect(proj.style.left).toBe('25%');
    expect(proj.style.width).toBe('50%');
  });

  it('positions now-mark at elapsed fraction', () => {
    const { container } = render(
      <BudgetRunwayBar spent={30} projected={60} cap={100} elapsedFrac={0.4} />,
    );
    const mark = container.querySelector('.niuu-budget-runway__now-mark') as HTMLElement;
    expect(mark.style.left).toBe('40%');
  });

  it('adds --over class on projected element when projected > cap', () => {
    const { container } = render(
      <BudgetRunwayBar spent={80} projected={120} cap={100} elapsedFrac={0.7} />,
    );
    const proj = container.querySelector('.niuu-budget-runway__proj');
    expect(proj?.classList.contains('niuu-budget-runway__proj--over')).toBe(true);
  });

  it('does not add --over class when projected <= cap', () => {
    const { container } = render(
      <BudgetRunwayBar spent={40} projected={90} cap={100} elapsedFrac={0.5} />,
    );
    const proj = container.querySelector('.niuu-budget-runway__proj');
    expect(proj?.classList.contains('niuu-budget-runway__proj--over')).toBe(false);
  });

  it('handles zero cap gracefully', () => {
    const { container } = render(
      <BudgetRunwayBar spent={0} projected={0} cap={0} elapsedFrac={0.5} />,
    );
    const spent = container.querySelector('.niuu-budget-runway__spent') as HTMLElement;
    expect(spent.style.width).toBe('0%');
  });

  it('clamps spent to 100% when over cap', () => {
    const { container } = render(
      <BudgetRunwayBar spent={150} projected={200} cap={100} elapsedFrac={0.8} />,
    );
    const spent = container.querySelector('.niuu-budget-runway__spent') as HTMLElement;
    expect(spent.style.width).toBe('100%');
  });

  it('clamps elapsedFrac to [0, 1] range', () => {
    const { container: c1 } = render(
      <BudgetRunwayBar spent={40} projected={70} cap={100} elapsedFrac={-0.5} />,
    );
    const mark1 = c1.querySelector('.niuu-budget-runway__now-mark') as HTMLElement;
    expect(mark1.style.left).toBe('0%');

    const { container: c2 } = render(
      <BudgetRunwayBar spent={40} projected={70} cap={100} elapsedFrac={1.5} />,
    );
    const mark2 = c2.querySelector('.niuu-budget-runway__now-mark') as HTMLElement;
    expect(mark2.style.left).toBe('100%');
  });

  it('forwards className', () => {
    render(
      <BudgetRunwayBar spent={40} projected={70} cap={100} elapsedFrac={0.5} className="extra" />,
    );
    expect(screen.getByRole('meter')).toHaveClass('extra');
  });
});
