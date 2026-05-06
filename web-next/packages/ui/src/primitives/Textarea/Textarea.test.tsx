import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { Field } from '../Field/Field';
import { Textarea } from './Textarea';

describe('Textarea', () => {
  it('renders a textarea element', () => {
    render(<Textarea />);
    expect(screen.getByRole('textbox')).toBeInTheDocument();
  });

  it('applies niuu-textarea base class', () => {
    render(<Textarea />);
    expect(screen.getByRole('textbox')).toHaveClass('niuu-textarea');
  });

  it('defaults to 4 rows', () => {
    render(<Textarea />);
    expect(screen.getByRole('textbox')).toHaveAttribute('rows', '4');
  });

  it('accepts custom rows prop', () => {
    render(<Textarea rows={8} />);
    expect(screen.getByRole('textbox')).toHaveAttribute('rows', '8');
  });

  it('forwards ref', () => {
    let ref: HTMLTextAreaElement | null = null;
    render(
      <Textarea
        ref={(el) => {
          ref = el;
        }}
      />,
    );
    expect(ref).toBeInstanceOf(HTMLTextAreaElement);
  });

  it('forwards native HTML attributes', () => {
    render(<Textarea placeholder="Write something" data-testid="ta" />);
    const ta = screen.getByTestId('ta');
    expect(ta).toHaveAttribute('placeholder', 'Write something');
  });

  it('applies custom className alongside base class', () => {
    render(<Textarea className="extra" />);
    expect(screen.getByRole('textbox')).toHaveClass('niuu-textarea', 'extra');
  });

  it('sets aria-invalid and error class when inside Field with error', () => {
    render(
      <Field label="Notes" error="Too short">
        <Textarea />
      </Field>,
    );
    const ta = screen.getByRole('textbox');
    expect(ta).toHaveAttribute('aria-invalid', 'true');
    expect(ta).toHaveClass('niuu-textarea--error');
  });

  it('does not set aria-invalid when no error', () => {
    render(
      <Field label="Notes">
        <Textarea />
      </Field>,
    );
    const ta = screen.getByRole('textbox');
    expect(ta).not.toHaveAttribute('aria-invalid');
    expect(ta).not.toHaveClass('niuu-textarea--error');
  });

  it('uses fieldId from context when no explicit id passed', () => {
    render(
      <Field label="Notes">
        <Textarea />
      </Field>,
    );
    const label = screen.getByText('Notes');
    const ta = screen.getByRole('textbox');
    expect(ta.id).toBe(label.getAttribute('for'));
  });

  it('is disabled when disabled prop passed', () => {
    render(<Textarea disabled />);
    expect(screen.getByRole('textbox')).toBeDisabled();
  });

  it('wires aria-describedby from Field error', () => {
    render(
      <Field label="Notes" error="Required">
        <Textarea />
      </Field>,
    );
    const ta = screen.getByRole('textbox');
    const alert = screen.getByRole('alert');
    expect(ta.getAttribute('aria-describedby')).toContain(alert.id);
  });
});
