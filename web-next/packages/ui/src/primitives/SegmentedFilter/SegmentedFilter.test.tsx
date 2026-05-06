import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { SegmentedFilter } from './SegmentedFilter';

const OPTIONS = [
  { value: 'all', label: 'All', count: 10 },
  { value: 'active', label: 'Active', count: 6 },
  { value: 'done', label: 'Done', count: 4 },
] as const;

function setup() {
  return userEvent.setup();
}

describe('SegmentedFilter', () => {
  it('renders all options as buttons', () => {
    render(<SegmentedFilter options={OPTIONS} value="all" onChange={() => {}} />);
    expect(screen.getByRole('button', { name: /All/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Active/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Done/ })).toBeInTheDocument();
  });

  it('marks the active option with aria-pressed=true', () => {
    render(<SegmentedFilter options={OPTIONS} value="active" onChange={() => {}} />);
    expect(screen.getByRole('button', { name: /Active/ })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: /All/ })).toHaveAttribute('aria-pressed', 'false');
  });

  it('calls onChange when a button is clicked', async () => {
    const user = setup();
    const onChange = vi.fn();
    render(<SegmentedFilter options={OPTIONS} value="all" onChange={onChange} />);
    await user.click(screen.getByRole('button', { name: /Done/ }));
    expect(onChange).toHaveBeenCalledWith('done');
  });

  it('renders counts when provided', () => {
    render(<SegmentedFilter options={OPTIONS} value="all" onChange={() => {}} />);
    expect(screen.getByText('10')).toBeInTheDocument();
    expect(screen.getByText('6')).toBeInTheDocument();
    expect(screen.getByText('4')).toBeInTheDocument();
  });

  it('renders without counts when not provided', () => {
    const noCounts = [
      { value: 'a', label: 'Alpha' },
      { value: 'b', label: 'Beta' },
    ];
    render(<SegmentedFilter options={noCounts} value="a" onChange={() => {}} />);
    expect(screen.getByRole('button', { name: 'Alpha' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Beta' })).toBeInTheDocument();
  });

  it('renders the group with a custom aria-label', () => {
    render(
      <SegmentedFilter
        options={OPTIONS}
        value="all"
        onChange={() => {}}
        aria-label="Filter sessions"
      />,
    );
    expect(screen.getByRole('group', { name: 'Filter sessions' })).toBeInTheDocument();
  });

  it('applies custom className', () => {
    render(
      <SegmentedFilter options={OPTIONS} value="all" onChange={() => {}} className="extra-class" />,
    );
    expect(screen.getByRole('group')).toHaveClass('extra-class');
  });

  it('handles single option', () => {
    const single = [{ value: 'only', label: 'Only', count: 1 }];
    render(<SegmentedFilter options={single} value="only" onChange={() => {}} />);
    expect(screen.getByRole('button', { name: /Only/ })).toHaveAttribute('aria-pressed', 'true');
  });
});
