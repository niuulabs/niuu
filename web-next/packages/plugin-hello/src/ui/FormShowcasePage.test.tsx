import { render, screen, fireEvent, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { FormShowcasePage } from './FormShowcasePage';

describe('FormShowcasePage', () => {
  it('renders the form heading', () => {
    render(<FormShowcasePage />);
    expect(screen.getByText('Form Showcase')).toBeInTheDocument();
  });

  it('renders all form fields', () => {
    render(<FormShowcasePage />);
    expect(screen.getByLabelText(/Full name/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Email/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Bio/i)).toBeInTheDocument();
  });

  it('renders submit button', () => {
    render(<FormShowcasePage />);
    expect(screen.getByRole('button', { name: /submit/i })).toBeInTheDocument();
  });

  it('shows ValidationSummary heading when submitting empty form', async () => {
    const user = userEvent.setup();
    render(<FormShowcasePage />);
    await user.click(screen.getByRole('button', { name: /submit/i }));
    expect(screen.getByText('Please fix the following issues:')).toBeInTheDocument();
  });

  it('shows required field errors in summary after empty submit', async () => {
    const user = userEvent.setup();
    render(<FormShowcasePage />);
    await user.click(screen.getByRole('button', { name: /submit/i }));
    // ValidationSummary has class niuu-validation-summary
    const summary = document.querySelector('.niuu-validation-summary');
    expect(summary).not.toBeNull();
    expect(summary!.textContent).toMatch(/Full name is required/i);
    expect(summary!.textContent).toMatch(/Email is required/i);
    expect(summary!.textContent).toMatch(/Bio is required/i);
  });

  it('shows email validation error for invalid email', async () => {
    const user = userEvent.setup();
    render(<FormShowcasePage />);
    await user.type(screen.getByLabelText(/Full name/i), 'Jane');
    await user.type(screen.getByLabelText(/Email/i), 'not-an-email');
    await user.click(screen.getByRole('button', { name: /submit/i }));
    expect(screen.getAllByText(/valid email/i).length).toBeGreaterThan(0);
  });

  it('does not show ValidationSummary when form is initially rendered', () => {
    render(<FormShowcasePage />);
    expect(document.querySelector('.niuu-validation-summary')).toBeNull();
  });

  it('marks name field as aria-invalid after failed submit', async () => {
    const user = userEvent.setup();
    render(<FormShowcasePage />);
    await user.click(screen.getByRole('button', { name: /submit/i }));
    expect(screen.getByLabelText(/Full name/i)).toHaveAttribute('aria-invalid', 'true');
  });

  it('marks email field as aria-invalid after failed submit', async () => {
    const user = userEvent.setup();
    render(<FormShowcasePage />);
    await user.click(screen.getByRole('button', { name: /submit/i }));
    expect(screen.getByLabelText(/Email/i)).toHaveAttribute('aria-invalid', 'true');
  });

  describe('focus-jump from ValidationSummary', () => {
    beforeEach(() => {
      vi.spyOn(Element.prototype, 'scrollIntoView').mockImplementation(() => {});
    });

    afterEach(() => {
      vi.restoreAllMocks();
    });

    it('focuses the name field when its summary link is clicked', async () => {
      const user = userEvent.setup();
      render(<FormShowcasePage />);
      await user.click(screen.getByRole('button', { name: /submit/i }));

      const nameInput = screen.getByLabelText(/Full name/i);
      const focusSpy = vi.spyOn(nameInput, 'focus');

      // The ValidationSummary renders buttons for each error
      const summary = document.querySelector('.niuu-validation-summary')!;
      const summaryButton = within(summary as HTMLElement).getByRole('button', {
        name: /Full name.*required/i,
      });
      fireEvent.click(summaryButton);

      expect(focusSpy).toHaveBeenCalled();
    });
  });
});
