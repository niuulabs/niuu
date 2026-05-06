import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { ValidationSummary } from './ValidationSummary';

const ERRORS = [
  { id: 'field-name', label: 'Full name', message: 'Required' },
  { id: 'field-email', label: 'Email', message: 'Invalid email address' },
];

describe('ValidationSummary', () => {
  it('renders nothing when errors is empty', () => {
    const { container } = render(<ValidationSummary errors={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it('renders error list when errors provided', () => {
    render(<ValidationSummary errors={ERRORS} />);
    expect(screen.getByText(/Full name/)).toBeInTheDocument();
    expect(screen.getByText(/Email/)).toBeInTheDocument();
  });

  it('renders default heading', () => {
    render(<ValidationSummary errors={ERRORS} />);
    expect(screen.getByText('Please fix the following issues:')).toBeInTheDocument();
  });

  it('renders custom heading when provided', () => {
    render(<ValidationSummary errors={ERRORS} heading="Fix these errors:" />);
    expect(screen.getByText('Fix these errors:')).toBeInTheDocument();
  });

  it('has role=alert for screen reader announcement', () => {
    render(<ValidationSummary errors={ERRORS} />);
    expect(screen.getByRole('alert')).toBeInTheDocument();
  });

  it('renders each error as a clickable button', () => {
    render(<ValidationSummary errors={ERRORS} />);
    const buttons = screen.getAllByRole('button');
    expect(buttons).toHaveLength(2);
  });

  it('includes field label in button text', () => {
    render(<ValidationSummary errors={ERRORS} />);
    expect(screen.getByRole('button', { name: /Full name/i })).toBeInTheDocument();
  });

  it('includes error message in button text', () => {
    render(<ValidationSummary errors={ERRORS} />);
    expect(screen.getByRole('button', { name: /Invalid email address/i })).toBeInTheDocument();
  });

  it('applies niuu-validation-summary class', () => {
    render(<ValidationSummary errors={ERRORS} />);
    expect(screen.getByRole('alert')).toHaveClass('niuu-validation-summary');
  });

  it('applies custom className', () => {
    render(<ValidationSummary errors={ERRORS} className="custom" />);
    expect(screen.getByRole('alert')).toHaveClass('custom');
  });

  describe('focus-jump behaviour', () => {
    beforeEach(() => {
      // Stub document.getElementById and focus/scrollIntoView
      vi.spyOn(Element.prototype, 'scrollIntoView').mockImplementation(() => {});
    });

    afterEach(() => {
      vi.restoreAllMocks();
    });

    it('calls focus on the target field when button clicked', () => {
      const mockInput = document.createElement('input');
      mockInput.id = 'field-name';
      const focusSpy = vi.spyOn(mockInput, 'focus');
      vi.spyOn(document, 'getElementById').mockReturnValue(mockInput);

      render(<ValidationSummary errors={ERRORS} />);
      fireEvent.click(screen.getByRole('button', { name: /Full name/i }));

      expect(document.getElementById).toHaveBeenCalledWith('field-name');
      expect(focusSpy).toHaveBeenCalled();
    });

    it('calls scrollIntoView on the target field when button clicked', () => {
      const mockInput = document.createElement('input');
      mockInput.id = 'field-name';
      const scrollSpy = vi.spyOn(mockInput, 'scrollIntoView').mockImplementation(() => {});
      vi.spyOn(mockInput, 'focus').mockImplementation(() => {});
      vi.spyOn(document, 'getElementById').mockReturnValue(mockInput);

      render(<ValidationSummary errors={ERRORS} />);
      fireEvent.click(screen.getByRole('button', { name: /Full name/i }));

      expect(scrollSpy).toHaveBeenCalled();
    });

    it('does nothing when target field id not found in DOM', () => {
      vi.spyOn(document, 'getElementById').mockReturnValue(null);

      render(<ValidationSummary errors={ERRORS} />);
      expect(() => {
        fireEvent.click(screen.getByRole('button', { name: /Full name/i }));
      }).not.toThrow();
    });
  });
});
