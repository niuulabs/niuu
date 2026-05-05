import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Meter } from './Meter';

describe('Meter', () => {
  it('renders empty state for null values', () => {
    render(<Meter used={null} limit={null} label="CPU" />);
    expect(screen.getByText('—')).toBeInTheDocument();
    expect(screen.getByRole('meter')).toHaveAttribute('aria-valuenow', '0');
  });

  it('renders cool level when under 60%', () => {
    render(<Meter used={30} limit={100} label="CPU" />);
    expect(screen.getByTestId('meter')).toHaveAttribute('data-level', 'cool');
    expect(screen.getByRole('meter')).toHaveAttribute('aria-valuenow', '30');
  });

  it('renders warm level between 60-85%', () => {
    render(<Meter used={70} limit={100} label="Mem" />);
    expect(screen.getByTestId('meter')).toHaveAttribute('data-level', 'warm');
  });

  it('renders hot level above 85%', () => {
    render(<Meter used={90} limit={100} label="GPU" />);
    expect(screen.getByTestId('meter')).toHaveAttribute('data-level', 'hot');
  });

  it('shows used/limit text with unit', () => {
    render(<Meter used={4} limit={8} unit="c" label="CPU" />);
    expect(screen.getByText('4c/8c')).toBeInTheDocument();
  });

  it('clamps to 100% when over limit', () => {
    render(<Meter used={120} limit={100} label="Disk" />);
    expect(screen.getByRole('meter')).toHaveAttribute('aria-valuenow', '100');
  });

  it('renders without label', () => {
    render(<Meter used={50} limit={100} />);
    expect(screen.getByTestId('meter')).toBeInTheDocument();
  });

  it('uses custom critical threshold', () => {
    render(<Meter used={75} limit={100} critical={0.7} label="test" />);
    expect(screen.getByTestId('meter')).toHaveAttribute('data-level', 'hot');
  });

  it('renders empty state for zero limit', () => {
    render(<Meter used={50} limit={0} label="Zero" />);
    expect(screen.getByRole('meter')).toHaveAttribute('aria-valuenow', '0');
  });

  it('applies custom className', () => {
    render(<Meter used={50} limit={100} className="custom-class" />);
    expect(screen.getByTestId('meter')).toHaveClass('custom-class');
  });
});
