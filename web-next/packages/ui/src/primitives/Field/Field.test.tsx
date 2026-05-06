import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { Field, useField } from './Field';
import { Input } from '../Input/Input';

describe('Field', () => {
  it('renders label and children', () => {
    render(
      <Field label="Full name">
        <input type="text" />
      </Field>,
    );
    expect(screen.getByText('Full name')).toBeInTheDocument();
  });

  it('associates label with child via htmlFor/id from context', () => {
    render(
      <Field label="Email">
        <Input />
      </Field>,
    );
    const label = screen.getByText('Email');
    const input = screen.getByRole('textbox');
    expect(label.getAttribute('for')).toBe(input.id);
  });

  it('renders hint when provided', () => {
    render(
      <Field label="Password" hint="At least 8 characters">
        <Input type="password" />
      </Field>,
    );
    expect(screen.getByText('At least 8 characters')).toBeInTheDocument();
  });

  it('renders error message with role=alert when error provided', () => {
    render(
      <Field label="Email" error="Invalid email address">
        <Input />
      </Field>,
    );
    const error = screen.getByRole('alert');
    expect(error).toHaveTextContent('Invalid email address');
  });

  it('does not render hint element when hint is absent', () => {
    render(
      <Field label="Name">
        <Input />
      </Field>,
    );
    expect(screen.queryByText(/hint/i)).not.toBeInTheDocument();
  });

  it('does not render error element when error is absent', () => {
    render(
      <Field label="Name">
        <Input />
      </Field>,
    );
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('adds niuu-field--error class when error is present', () => {
    const { container } = render(
      <Field label="Name" error="Required">
        <Input />
      </Field>,
    );
    expect(container.firstChild).toHaveClass('niuu-field--error');
  });

  it('does not add error class when no error', () => {
    const { container } = render(
      <Field label="Name">
        <Input />
      </Field>,
    );
    expect(container.firstChild).not.toHaveClass('niuu-field--error');
  });

  it('renders required indicator when required=true', () => {
    render(
      <Field label="Name" required>
        <Input />
      </Field>,
    );
    expect(screen.getByText('*', { exact: false })).toBeInTheDocument();
  });

  it('does not render required indicator when required=false', () => {
    render(
      <Field label="Name" required={false}>
        <Input />
      </Field>,
    );
    expect(screen.queryByText('*')).not.toBeInTheDocument();
  });

  it('applies custom className to wrapper', () => {
    const { container } = render(
      <Field label="Name" className="custom-class">
        <Input />
      </Field>,
    );
    expect(container.firstChild).toHaveClass('custom-class');
  });

  it('uses explicit id prop for label htmlFor', () => {
    render(
      <Field id="my-field" label="Name">
        <Input />
      </Field>,
    );
    const label = screen.getByText('Name');
    expect(label.getAttribute('for')).toBe('my-field');
  });

  it('wires aria-describedby to hint id when hint present', () => {
    render(
      <Field label="Name" hint="Some hint">
        <Input />
      </Field>,
    );
    const input = screen.getByRole('textbox');
    const hint = screen.getByText('Some hint');
    expect(input.getAttribute('aria-describedby')).toContain(hint.id);
  });

  it('wires aria-describedby to error id when error present', () => {
    render(
      <Field label="Name" error="Some error">
        <Input />
      </Field>,
    );
    const input = screen.getByRole('textbox');
    const error = screen.getByRole('alert');
    expect(input.getAttribute('aria-describedby')).toContain(error.id);
  });

  it('wires both hint and error ids when both present', () => {
    render(
      <Field label="Name" hint="A hint" error="An error">
        <Input />
      </Field>,
    );
    const input = screen.getByRole('textbox');
    const describedBy = input.getAttribute('aria-describedby') ?? '';
    expect(describedBy.split(' ').length).toBe(2);
  });
});

describe('useField outside Field', () => {
  it('returns default context values outside Field', () => {
    let ctx: ReturnType<typeof useField> | null = null;
    function Inspector() {
      ctx = useField();
      return null;
    }
    render(<Inspector />);
    expect(ctx).not.toBeNull();
    expect(ctx!.hasError).toBe(false);
    expect(ctx!.required).toBe(false);
  });
});
