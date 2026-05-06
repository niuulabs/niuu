import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { PlanGuidanceRail } from './PlanGuidanceRail';

describe('PlanGuidanceRail', () => {
  it('renders both guidance card titles', () => {
    render(<PlanGuidanceRail />);
    expect(screen.getByText('How Plan works')).toBeInTheDocument();
    expect(screen.getByText('What a planning raid produces')).toBeInTheDocument();
  });

  it('renders numbered step indicators for the How Plan works card', () => {
    render(<PlanGuidanceRail />);
    expect(screen.getByText('1')).toBeInTheDocument();
    expect(screen.getByText('2')).toBeInTheDocument();
    expect(screen.getByText('3')).toBeInTheDocument();
    expect(screen.getByText('4')).toBeInTheDocument();
  });

  it('renders the correct total number of list items', () => {
    render(<PlanGuidanceRail />);
    // 4 items in How Plan works + 5 items in What a planning raid produces
    const items = screen.getAllByRole('listitem');
    expect(items.length).toBe(9);
  });

  it('has the Plan guidance landmark label', () => {
    render(<PlanGuidanceRail />);
    expect(screen.getByRole('complementary', { name: 'Plan guidance' })).toBeInTheDocument();
  });

  it('renders bullet dots for the non-numbered card', () => {
    render(<PlanGuidanceRail />);
    const dots = screen.getAllByText('·');
    expect(dots.length).toBe(5);
  });
});
