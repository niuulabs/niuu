import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { KpiCard } from './KpiCard';
import { KpiStrip } from './KpiStrip';

describe('KpiCard', () => {
  it('renders label', () => {
    render(<KpiCard label="Total Sessions" value={42} />);
    expect(screen.getByText('Total Sessions')).toBeInTheDocument();
  });

  it('renders value', () => {
    render(<KpiCard label="Sessions" value={42} />);
    expect(screen.getByText('42')).toBeInTheDocument();
  });

  it('renders string value', () => {
    render(<KpiCard label="Status" value="healthy" />);
    expect(screen.getByText('healthy')).toBeInTheDocument();
  });

  it('renders delta when provided', () => {
    render(<KpiCard label="Sessions" value={42} delta="+12%" />);
    expect(screen.getByText('+12%')).toBeInTheDocument();
  });

  it('does not render delta element when delta is undefined', () => {
    const { container } = render(<KpiCard label="Sessions" value={42} />);
    expect(container.querySelector('.niuu-kpi-card__delta')).not.toBeInTheDocument();
  });

  it('applies up trend class', () => {
    const { container } = render(
      <KpiCard label="Sessions" value={42} delta="+12%" deltaTrend="up" />,
    );
    expect(container.querySelector('.niuu-kpi-card__delta--up')).toBeInTheDocument();
  });

  it('applies down trend class', () => {
    const { container } = render(
      <KpiCard label="Sessions" value={42} delta="-5%" deltaTrend="down" />,
    );
    expect(container.querySelector('.niuu-kpi-card__delta--down')).toBeInTheDocument();
  });

  it('defaults to neutral trend', () => {
    const { container } = render(<KpiCard label="Sessions" value={42} delta="0%" />);
    expect(container.querySelector('.niuu-kpi-card__delta--neutral')).toBeInTheDocument();
  });

  it('renders up arrow for up trend', () => {
    render(<KpiCard label="Sessions" value={42} delta="+5%" deltaTrend="up" />);
    expect(screen.getByText('▲')).toBeInTheDocument();
  });

  it('renders down arrow for down trend', () => {
    render(<KpiCard label="Sessions" value={42} delta="-5%" deltaTrend="down" />);
    expect(screen.getByText('▼')).toBeInTheDocument();
  });

  it('renders dash for neutral trend', () => {
    render(<KpiCard label="Sessions" value={42} delta="0%" deltaTrend="neutral" />);
    expect(screen.getByText('—')).toBeInTheDocument();
  });

  it('renders deltaLabel when provided', () => {
    render(<KpiCard label="Sessions" value={42} delta="+5%" deltaLabel="vs last week" />);
    expect(screen.getByText('vs last week')).toBeInTheDocument();
  });

  it('does not render deltaLabel when not provided', () => {
    render(<KpiCard label="Sessions" value={42} delta="+5%" />);
    expect(document.querySelector('.niuu-kpi-card__delta-label')).not.toBeInTheDocument();
  });

  it('renders sparkline slot', () => {
    render(
      <KpiCard label="Sessions" value={42} sparkline={<span data-testid="spark">chart</span>} />,
    );
    expect(screen.getByTestId('spark')).toBeInTheDocument();
  });

  it('does not render sparkline slot when not provided', () => {
    const { container } = render(<KpiCard label="Sessions" value={42} />);
    expect(container.querySelector('.niuu-kpi-card__sparkline')).not.toBeInTheDocument();
  });

  it('applies custom className', () => {
    const { container } = render(<KpiCard label="Sessions" value={42} className="wide" />);
    expect(container.firstChild).toHaveClass('wide');
  });
});

describe('KpiStrip', () => {
  it('renders children', () => {
    render(
      <KpiStrip>
        <KpiCard label="A" value={1} />
        <KpiCard label="B" value={2} />
      </KpiStrip>,
    );
    expect(screen.getByText('A')).toBeInTheDocument();
    expect(screen.getByText('B')).toBeInTheDocument();
  });

  it('has group role', () => {
    render(
      <KpiStrip>
        <KpiCard label="A" value={1} />
      </KpiStrip>,
    );
    expect(screen.getByRole('group')).toBeInTheDocument();
  });

  it('applies custom className', () => {
    const { container } = render(
      <KpiStrip className="custom">
        <KpiCard label="A" value={1} />
      </KpiStrip>,
    );
    expect(container.firstChild).toHaveClass('custom');
  });
});
