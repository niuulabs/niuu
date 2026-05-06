import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { FilterBar } from './FilterBar';
import { FilterChip } from './FilterChip';
import { FilterToggle } from './FilterToggle';

// ── FilterChip ──────────────────────────────────────────

describe('FilterChip', () => {
  it('renders label and value', () => {
    render(<FilterChip label="status" value="active" onRemove={() => {}} />);
    expect(screen.getByText('status')).toBeInTheDocument();
    expect(screen.getByText('active')).toBeInTheDocument();
  });

  it('calls onRemove when remove button is clicked', async () => {
    const onRemove = vi.fn();
    render(<FilterChip label="status" value="active" onRemove={onRemove} />);
    await userEvent.click(screen.getByRole('button', { name: /Remove filter status/ }));
    expect(onRemove).toHaveBeenCalledOnce();
  });

  it('applies custom className', () => {
    const { container } = render(
      <FilterChip label="k" value="v" onRemove={() => {}} className="custom" />,
    );
    expect(container.firstChild).toHaveClass('custom');
  });
});

// ── FilterToggle ────────────────────────────────────────

describe('FilterToggle', () => {
  it('renders label', () => {
    render(<FilterToggle label="Active only" active={false} onChange={() => {}} />);
    expect(screen.getByText('Active only')).toBeInTheDocument();
  });

  it('has role=switch', () => {
    render(<FilterToggle label="Active only" active={false} onChange={() => {}} />);
    expect(screen.getByRole('switch')).toBeInTheDocument();
  });

  it('reflects active state via aria-checked', () => {
    render(<FilterToggle label="Active only" active={true} onChange={() => {}} />);
    expect(screen.getByRole('switch')).toHaveAttribute('aria-checked', 'true');
  });

  it('reflects inactive state via aria-checked=false', () => {
    render(<FilterToggle label="Active only" active={false} onChange={() => {}} />);
    expect(screen.getByRole('switch')).toHaveAttribute('aria-checked', 'false');
  });

  it('calls onChange with toggled value when clicked (inactive → active)', async () => {
    const onChange = vi.fn();
    render(<FilterToggle label="Active only" active={false} onChange={onChange} />);
    await userEvent.click(screen.getByRole('switch'));
    expect(onChange).toHaveBeenCalledWith(true);
  });

  it('calls onChange with toggled value when clicked (active → inactive)', async () => {
    const onChange = vi.fn();
    render(<FilterToggle label="Active only" active={true} onChange={onChange} />);
    await userEvent.click(screen.getByRole('switch'));
    expect(onChange).toHaveBeenCalledWith(false);
  });

  it('applies active class when active', () => {
    const { container } = render(
      <FilterToggle label="Active only" active={true} onChange={() => {}} />,
    );
    expect(container.firstChild).toHaveClass('niuu-filter-toggle--active');
  });

  it('does not apply active class when inactive', () => {
    const { container } = render(
      <FilterToggle label="Active only" active={false} onChange={() => {}} />,
    );
    expect(container.firstChild).not.toHaveClass('niuu-filter-toggle--active');
  });

  it('applies custom className', () => {
    const { container } = render(
      <FilterToggle label="Active only" active={false} onChange={() => {}} className="my-toggle" />,
    );
    expect(container.firstChild).toHaveClass('my-toggle');
  });
});

// ── FilterBar ────────────────────────────────────────────

describe('FilterBar', () => {
  it('renders search input', () => {
    render(<FilterBar value={{}} onChange={() => {}} />);
    expect(screen.getByRole('searchbox', { name: 'Search' })).toBeInTheDocument();
  });

  it('shows the search value', () => {
    render(<FilterBar value={{ q: 'hello' }} onChange={() => {}} />);
    expect(screen.getByRole('searchbox')).toHaveValue('hello');
  });

  it('calls onChange when search input changes', async () => {
    const onChange = vi.fn();
    render(<FilterBar value={{}} onChange={onChange} />);
    await userEvent.type(screen.getByRole('searchbox'), 'x');
    expect(onChange).toHaveBeenCalledWith({ q: 'x' });
  });

  it('uses custom searchKey', async () => {
    const onChange = vi.fn();
    render(<FilterBar value={{}} onChange={onChange} searchKey="name" />);
    await userEvent.type(screen.getByRole('searchbox'), 'a');
    expect(onChange).toHaveBeenCalledWith({ name: 'a' });
  });

  it('shows custom placeholder', () => {
    render(<FilterBar value={{}} onChange={() => {}} placeholder="Find sessions…" />);
    expect(screen.getByPlaceholderText('Find sessions…')).toBeInTheDocument();
  });

  it('renders chips for non-search filter keys', () => {
    render(<FilterBar value={{ status: 'active' }} onChange={() => {}} />);
    expect(screen.getByText('active')).toBeInTheDocument();
  });

  it('does not render chips row when no active filters', () => {
    const { container } = render(<FilterBar value={{}} onChange={() => {}} />);
    expect(container.querySelector('.niuu-filter-bar__chips')).not.toBeInTheDocument();
  });

  it('removes chip key from state when remove is clicked', async () => {
    const onChange = vi.fn();
    render(<FilterBar value={{ q: '', status: 'active' }} onChange={onChange} />);
    await userEvent.click(screen.getByRole('button', { name: /Remove filter status/ }));
    expect(onChange).toHaveBeenCalledWith({ q: '' });
  });

  it('shows clear button when search is non-empty', () => {
    render(<FilterBar value={{ q: 'hello' }} onChange={() => {}} />);
    expect(screen.getByRole('button', { name: 'Clear all filters' })).toBeInTheDocument();
  });

  it('shows clear button when chips are present', () => {
    render(<FilterBar value={{ status: 'active' }} onChange={() => {}} />);
    expect(screen.getByRole('button', { name: 'Clear all filters' })).toBeInTheDocument();
  });

  it('does not show clear button when everything is empty', () => {
    render(<FilterBar value={{}} onChange={() => {}} />);
    expect(screen.queryByRole('button', { name: 'Clear all filters' })).not.toBeInTheDocument();
  });

  it('clears all filters when clear button is clicked', async () => {
    const onChange = vi.fn();
    render(<FilterBar value={{ q: 'hello', status: 'active' }} onChange={onChange} />);
    await userEvent.click(screen.getByRole('button', { name: 'Clear all filters' }));
    expect(onChange).toHaveBeenCalledWith({});
  });

  it('uses custom activeFilters when provided', () => {
    render(
      <FilterBar
        value={{ x: 'foo' }}
        onChange={() => {}}
        activeFilters={[{ key: 'x', label: 'Type', value: 'foo' }]}
      />,
    );
    expect(screen.getByText('Type')).toBeInTheDocument();
    expect(screen.getByText('foo')).toBeInTheDocument();
  });

  it('renders children controls', () => {
    render(
      <FilterBar value={{}} onChange={() => {}}>
        <button>Options</button>
      </FilterBar>,
    );
    expect(screen.getByRole('button', { name: 'Options' })).toBeInTheDocument();
  });

  it('applies custom className', () => {
    const { container } = render(<FilterBar value={{}} onChange={() => {}} className="custom" />);
    expect(container.firstChild).toHaveClass('custom');
  });

  it('filter serialization: empty string values are not shown as chips', () => {
    render(<FilterBar value={{ q: '', status: '' }} onChange={() => {}} />);
    const chips = document.querySelectorAll('.niuu-filter-chip');
    expect(chips.length).toBe(0);
  });
});
