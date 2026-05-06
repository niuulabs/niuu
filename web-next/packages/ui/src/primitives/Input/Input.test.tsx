import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { Field } from '../Field/Field';
import { Input } from './Input';

describe('Input', () => {
  it('renders an input element', () => {
    render(<Input />);
    expect(screen.getByRole('textbox')).toBeInTheDocument();
  });

  it('applies niuu-input base class', () => {
    render(<Input />);
    expect(screen.getByRole('textbox')).toHaveClass('niuu-input');
  });

  it('forwards ref', () => {
    let ref: HTMLInputElement | null = null;
    render(
      <Input
        ref={(el) => {
          ref = el;
        }}
      />,
    );
    expect(ref).toBeInstanceOf(HTMLInputElement);
  });

  it('forwards native HTML attributes', () => {
    render(<Input placeholder="Enter text" type="email" data-testid="email-input" />);
    const input = screen.getByTestId('email-input');
    expect(input).toHaveAttribute('placeholder', 'Enter text');
    expect(input).toHaveAttribute('type', 'email');
  });

  it('applies custom className alongside base class', () => {
    render(<Input className="extra" />);
    const input = screen.getByRole('textbox');
    expect(input).toHaveClass('niuu-input');
    expect(input).toHaveClass('extra');
  });

  it('sets aria-invalid and error class when inside Field with error', () => {
    render(
      <Field label="Name" error="Required">
        <Input />
      </Field>,
    );
    const input = screen.getByRole('textbox');
    expect(input).toHaveAttribute('aria-invalid', 'true');
    expect(input).toHaveClass('niuu-input--error');
  });

  it('does not set aria-invalid when no error', () => {
    render(
      <Field label="Name">
        <Input />
      </Field>,
    );
    const input = screen.getByRole('textbox');
    expect(input).not.toHaveAttribute('aria-invalid');
    expect(input).not.toHaveClass('niuu-input--error');
  });

  it('uses fieldId from context when no explicit id passed', () => {
    render(
      <Field label="Name">
        <Input />
      </Field>,
    );
    const label = screen.getByText('Name');
    const input = screen.getByRole('textbox');
    expect(input.id).toBe(label.getAttribute('for'));
  });

  it('uses explicit id prop over context id', () => {
    render(
      <Field label="Name">
        <Input id="my-custom-id" />
      </Field>,
    );
    const input = screen.getByRole('textbox');
    expect(input.id).toBe('my-custom-id');
  });

  it('is disabled when disabled prop passed', () => {
    render(<Input disabled />);
    expect(screen.getByRole('textbox')).toBeDisabled();
  });
});
