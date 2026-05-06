import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { Toggle } from './Toggle';

describe('Toggle', () => {
  it('renders with aria-checked false when unchecked', () => {
    render(<Toggle checked={false} onChange={vi.fn()} label="Test toggle" />);
    expect(screen.getByRole('switch')).toHaveAttribute('aria-checked', 'false');
  });

  it('renders with aria-checked true when checked', () => {
    render(<Toggle checked={true} onChange={vi.fn()} label="Test toggle" />);
    expect(screen.getByRole('switch')).toHaveAttribute('aria-checked', 'true');
  });

  it('calls onChange with negated value on click', () => {
    const onChange = vi.fn();
    render(<Toggle checked={false} onChange={onChange} label="Test toggle" />);
    fireEvent.click(screen.getByRole('switch'));
    expect(onChange).toHaveBeenCalledWith(true);
  });

  it('is findable by label', () => {
    render(<Toggle checked={false} onChange={vi.fn()} label="My feature" />);
    expect(screen.getByRole('switch', { name: 'My feature' })).toBeInTheDocument();
  });

  it('sets data-accent to brand by default', () => {
    render(<Toggle checked={false} onChange={vi.fn()} label="Test toggle" />);
    expect(screen.getByRole('switch')).toHaveAttribute('data-accent', 'brand');
  });

  it('sets data-accent to purple when specified', () => {
    render(<Toggle checked={false} onChange={vi.fn()} label="Test toggle" accent="purple" />);
    expect(screen.getByRole('switch')).toHaveAttribute('data-accent', 'purple');
  });

  it('is disabled when disabled prop is true', () => {
    render(<Toggle checked={false} onChange={vi.fn()} label="Test toggle" disabled />);
    expect(screen.getByRole('switch')).toBeDisabled();
  });
});
