import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { EventPicker } from './EventPicker';
import type { EventSpec } from '@niuulabs/domain';

const CATALOG: EventSpec[] = [
  { name: 'code.changed', schema: { file: 'string' } },
  { name: 'review.completed', schema: {} },
  { name: 'qa.completed', schema: {} },
];

describe('EventPicker', () => {
  it('renders the input with current value', () => {
    render(<EventPicker value="code.changed" onChange={vi.fn()} catalog={CATALOG} />);
    expect(screen.getByRole('combobox')).toHaveValue('code.changed');
  });

  it('renders placeholder when value is empty', () => {
    render(
      <EventPicker value="" onChange={vi.fn()} catalog={CATALOG} placeholder="Pick an event…" />,
    );
    expect(screen.getByPlaceholderText('Pick an event…')).toBeInTheDocument();
  });

  it('shows options on focus', () => {
    render(<EventPicker value="" onChange={vi.fn()} catalog={CATALOG} />);
    fireEvent.focus(screen.getByRole('combobox'));
    expect(screen.getByRole('listbox')).toBeInTheDocument();
    expect(screen.getByText('code.changed')).toBeInTheDocument();
    expect(screen.getByText('review.completed')).toBeInTheDocument();
  });

  it('filters options as the user types', () => {
    render(<EventPicker value="" onChange={vi.fn()} catalog={CATALOG} />);
    const input = screen.getByRole('combobox');
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: 'code' } });
    expect(screen.getByText('code.changed')).toBeInTheDocument();
    expect(screen.queryByText('review.completed')).not.toBeInTheDocument();
  });

  it('calls onChange when an option is clicked', () => {
    const onChange = vi.fn();
    render(<EventPicker value="" onChange={onChange} catalog={CATALOG} />);
    fireEvent.focus(screen.getByRole('combobox'));
    fireEvent.mouseDown(screen.getByText('code.changed'));
    expect(onChange).toHaveBeenCalledWith('code.changed');
  });

  it('shows "no events" when query matches nothing', () => {
    render(<EventPicker value="" onChange={vi.fn()} catalog={CATALOG} />);
    const input = screen.getByRole('combobox');
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: 'zzz-not-found' } });
    expect(screen.getByText('No events found')).toBeInTheDocument();
  });

  it('shows "Create" option when allowNew=true and query does not match', () => {
    render(<EventPicker value="" onChange={vi.fn()} catalog={CATALOG} allowNew />);
    const input = screen.getByRole('combobox');
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: 'new.event' } });
    expect(screen.getByText(/Create/)).toBeInTheDocument();
  });

  it('does not show "Create" when allowNew=false', () => {
    render(<EventPicker value="" onChange={vi.fn()} catalog={CATALOG} allowNew={false} />);
    const input = screen.getByRole('combobox');
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: 'new.event' } });
    expect(screen.queryByText(/Create/)).not.toBeInTheDocument();
  });

  it('shows "none" option when allowEmpty=true', () => {
    render(<EventPicker value="code.changed" onChange={vi.fn()} catalog={CATALOG} allowEmpty />);
    fireEvent.focus(screen.getByRole('combobox'));
    expect(screen.getByText('— none —')).toBeInTheDocument();
  });

  it('calls onChange with empty string when "none" is clicked', () => {
    const onChange = vi.fn();
    render(<EventPicker value="code.changed" onChange={onChange} catalog={CATALOG} allowEmpty />);
    fireEvent.focus(screen.getByRole('combobox'));
    fireEvent.mouseDown(screen.getByText('— none —'));
    expect(onChange).toHaveBeenCalledWith('');
  });

  it('selects first option on Enter', () => {
    const onChange = vi.fn();
    render(<EventPicker value="" onChange={onChange} catalog={CATALOG} />);
    const input = screen.getByRole('combobox');
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: 'code' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(onChange).toHaveBeenCalledWith('code.changed');
  });

  it('closes on Escape', () => {
    render(<EventPicker value="" onChange={vi.fn()} catalog={CATALOG} />);
    const input = screen.getByRole('combobox');
    fireEvent.focus(input);
    expect(screen.getByRole('listbox')).toBeInTheDocument();
    fireEvent.keyDown(input, { key: 'Escape' });
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  });

  it('is disabled when disabled prop is set', () => {
    render(<EventPicker value="" onChange={vi.fn()} catalog={CATALOG} disabled />);
    expect(screen.getByRole('combobox')).toBeDisabled();
  });
});
