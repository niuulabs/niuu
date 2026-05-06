import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi } from 'vitest';
import { Field } from '../Field/Field';
import { Select } from './Select';

const OPTIONS = [
  { value: 'a', label: 'Option A' },
  { value: 'b', label: 'Option B' },
  { value: 'c', label: 'Option C', disabled: true },
];

describe('Select', () => {
  it('renders the trigger button', () => {
    render(
      <Field label="Pick one">
        <Select options={OPTIONS} />
      </Field>,
    );
    expect(screen.getByRole('combobox')).toBeInTheDocument();
  });

  it('shows placeholder text by default', () => {
    render(
      <Field label="Pick one">
        <Select options={OPTIONS} placeholder="Choose…" />
      </Field>,
    );
    expect(screen.getByRole('combobox')).toHaveTextContent('Choose…');
  });

  it('shows selected value when value prop provided', () => {
    render(
      <Field label="Pick one">
        <Select options={OPTIONS} value="a" />
      </Field>,
    );
    expect(screen.getByRole('combobox')).toHaveTextContent('Option A');
  });

  it('applies niuu-select__trigger class', () => {
    render(
      <Field label="Pick one">
        <Select options={OPTIONS} />
      </Field>,
    );
    expect(screen.getByRole('combobox')).toHaveClass('niuu-select__trigger');
  });

  it('sets aria-invalid when inside Field with error', () => {
    render(
      <Field label="Pick one" error="Selection required">
        <Select options={OPTIONS} />
      </Field>,
    );
    expect(screen.getByRole('combobox')).toHaveAttribute('aria-invalid', 'true');
  });

  it('applies error class when Field has error', () => {
    render(
      <Field label="Pick one" error="Required">
        <Select options={OPTIONS} />
      </Field>,
    );
    expect(screen.getByRole('combobox')).toHaveClass('niuu-select__trigger--error');
  });

  it('does not set aria-invalid when no error', () => {
    render(
      <Field label="Pick one">
        <Select options={OPTIONS} />
      </Field>,
    );
    expect(screen.getByRole('combobox')).not.toHaveAttribute('aria-invalid');
  });

  it('is disabled when disabled prop passed', () => {
    render(
      <Field label="Pick one">
        <Select options={OPTIONS} disabled />
      </Field>,
    );
    expect(screen.getByRole('combobox')).toBeDisabled();
  });

  it('opens and shows options on click', async () => {
    const user = userEvent.setup();
    render(
      <Field label="Pick one">
        <Select options={OPTIONS} />
      </Field>,
    );
    await user.click(screen.getByRole('combobox'));
    expect(screen.getByText('Option A')).toBeInTheDocument();
    expect(screen.getByText('Option B')).toBeInTheDocument();
  });

  it('calls onValueChange when option selected', async () => {
    const onValueChange = vi.fn();
    const user = userEvent.setup();
    render(
      <Field label="Pick one">
        <Select options={OPTIONS} onValueChange={onValueChange} />
      </Field>,
    );
    await user.click(screen.getByRole('combobox'));
    await user.click(screen.getByText('Option A'));
    expect(onValueChange).toHaveBeenCalledWith('a');
  });

  it('forwards aria-describedby from Field hint', () => {
    render(
      <Field label="Pick one" hint="Pick carefully">
        <Select options={OPTIONS} />
      </Field>,
    );
    const trigger = screen.getByRole('combobox');
    const hint = screen.getByText('Pick carefully');
    expect(trigger.getAttribute('aria-describedby')).toContain(hint.id);
  });

  it('applies custom className to trigger', () => {
    render(
      <Field label="Pick one">
        <Select options={OPTIONS} className="my-class" />
      </Field>,
    );
    expect(screen.getByRole('combobox')).toHaveClass('my-class');
  });
});
