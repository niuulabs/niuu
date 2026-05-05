import { cn } from '../../utils/cn';

export interface ValidationError {
  id: string;
  label: string;
  message: string;
}

export interface ValidationSummaryProps {
  errors: ValidationError[];
  heading?: string;
  className?: string;
}

function focusField(fieldId: string): void {
  const el = document.getElementById(fieldId);
  if (!el) return;
  el.scrollIntoView({ behavior: 'smooth', block: 'center' });
  el.focus();
}

export function ValidationSummary({
  errors,
  heading = 'Please fix the following issues:',
  className,
}: ValidationSummaryProps) {
  if (errors.length === 0) return null;

  return (
    <div
      className={cn('niuu-validation-summary', className)}
      role="alert"
      aria-live="polite"
      aria-atomic="true"
    >
      <p className="niuu-validation-summary__heading">{heading}</p>
      <ul className="niuu-validation-summary__list">
        {errors.map(({ id, label, message }) => (
          <li key={id} className="niuu-validation-summary__item">
            <button
              type="button"
              className="niuu-validation-summary__link"
              onClick={() => focusField(id)}
            >
              <span className="niuu-validation-summary__label">{label}:</span> {message}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
