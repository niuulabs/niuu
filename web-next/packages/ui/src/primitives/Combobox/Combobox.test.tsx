import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi } from 'vitest';
import { Field } from '../Field/Field';
import { Combobox } from './Combobox';

const OPTIONS = [
  { value: 'apple', label: 'Apple' },
  { value: 'banana', label: 'Banana' },
  { value: 'cherry', label: 'Cherry', disabled: true },
];

describe('Combobox', () => {
  it('renders an input element', () => {
    render(
      <Field label="Fruit">
        <Combobox options={OPTIONS} />
      </Field>,
    );
    expect(screen.getByRole('combobox')).toBeInTheDocument();
  });

  it('has role=combobox on the input', () => {
    render(<Combobox options={OPTIONS} />);
    expect(screen.getByRole('combobox')).toBeInTheDocument();
  });

  it('applies niuu-combobox__input class', () => {
    render(<Combobox options={OPTIONS} />);
    expect(screen.getByRole('combobox')).toHaveClass('niuu-combobox__input');
  });

  it('shows placeholder when provided', () => {
    render(<Combobox options={OPTIONS} placeholder="Find a fruit" />);
    expect(screen.getByRole('combobox')).toHaveAttribute('placeholder', 'Find a fruit');
  });

  it('shows selected label when value prop provided', () => {
    render(<Combobox options={OPTIONS} value="apple" />);
    expect(screen.getByRole('combobox')).toHaveValue('Apple');
  });

  it('opens listbox on focus', async () => {
    const user = userEvent.setup();
    render(<Combobox options={OPTIONS} />);
    await user.click(screen.getByRole('combobox'));
    expect(screen.getByRole('listbox')).toBeInTheDocument();
    expect(screen.getByText('Apple')).toBeInTheDocument();
    expect(screen.getByText('Banana')).toBeInTheDocument();
  });

  it('filters options when typing', async () => {
    const user = userEvent.setup();
    render(<Combobox options={OPTIONS} />);
    const input = screen.getByRole('combobox');
    await user.click(input);
    await user.type(input, 'ban');
    expect(screen.queryByText('Apple')).not.toBeInTheDocument();
    expect(screen.getByText('Banana')).toBeInTheDocument();
  });

  it('shows empty message when no options match', async () => {
    const user = userEvent.setup();
    render(<Combobox options={OPTIONS} emptyMessage="Nothing found" />);
    const input = screen.getByRole('combobox');
    await user.click(input);
    await user.type(input, 'zzz');
    expect(screen.getByText('Nothing found')).toBeInTheDocument();
  });

  it('calls onValueChange when option selected', async () => {
    const onValueChange = vi.fn();
    const user = userEvent.setup();
    render(<Combobox options={OPTIONS} onValueChange={onValueChange} />);
    await user.click(screen.getByRole('combobox'));
    fireEvent.mouseDown(screen.getByText('Apple'));
    expect(onValueChange).toHaveBeenCalledWith('apple');
  });

  it('closes listbox after selecting an option', async () => {
    const user = userEvent.setup();
    render(<Combobox options={OPTIONS} onValueChange={() => {}} />);
    await user.click(screen.getByRole('combobox'));
    expect(screen.getByRole('listbox')).toBeInTheDocument();
    fireEvent.mouseDown(screen.getByText('Apple'));
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  });

  it('sets aria-expanded=true when open', async () => {
    const user = userEvent.setup();
    render(<Combobox options={OPTIONS} />);
    const input = screen.getByRole('combobox');
    await user.click(input);
    expect(input).toHaveAttribute('aria-expanded', 'true');
  });

  it('sets aria-expanded=false when closed', () => {
    render(<Combobox options={OPTIONS} />);
    expect(screen.getByRole('combobox')).toHaveAttribute('aria-expanded', 'false');
  });

  it('sets aria-invalid when inside Field with error', () => {
    render(
      <Field label="Fruit" error="Required">
        <Combobox options={OPTIONS} />
      </Field>,
    );
    expect(screen.getByRole('combobox')).toHaveAttribute('aria-invalid', 'true');
  });

  it('applies error class when Field has error', () => {
    render(
      <Field label="Fruit" error="Required">
        <Combobox options={OPTIONS} />
      </Field>,
    );
    expect(screen.getByRole('combobox')).toHaveClass('niuu-combobox__input--error');
  });

  it('does not set aria-invalid when no error', () => {
    render(
      <Field label="Fruit">
        <Combobox options={OPTIONS} />
      </Field>,
    );
    expect(screen.getByRole('combobox')).not.toHaveAttribute('aria-invalid');
  });

  it('is disabled when disabled prop passed', () => {
    render(<Combobox options={OPTIONS} disabled />);
    expect(screen.getByRole('combobox')).toBeDisabled();
  });

  it('closes on Escape key', async () => {
    const user = userEvent.setup();
    render(<Combobox options={OPTIONS} />);
    await user.click(screen.getByRole('combobox'));
    expect(screen.getByRole('listbox')).toBeInTheDocument();
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  });

  it('marks selected option with aria-selected=true', async () => {
    const user = userEvent.setup();
    render(<Combobox options={OPTIONS} value="apple" />);
    await user.click(screen.getByRole('combobox'));
    const appleOption = screen.getByRole('option', { name: 'Apple' });
    expect(appleOption).toHaveAttribute('aria-selected', 'true');
  });
});
