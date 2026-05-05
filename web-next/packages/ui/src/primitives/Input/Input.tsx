import { forwardRef, type InputHTMLAttributes } from 'react';
import { cn } from '../../utils/cn';
import { useField } from '../Field';

export type InputProps = InputHTMLAttributes<HTMLInputElement>;

export const Input = forwardRef<HTMLInputElement, InputProps>(
  ({ className, id: idProp, 'aria-describedby': ariaDescribedBy, ...props }, ref) => {
    const { id: fieldId, hintId, errorId, hasError } = useField();
    const id = idProp ?? fieldId;

    const describedBy = [hintId, errorId, ariaDescribedBy].filter(Boolean).join(' ') || undefined;

    return (
      <input
        ref={ref}
        id={id}
        className={cn(
          'niuu-form-control niuu-input',
          hasError && 'niuu-form-control--error niuu-input--error',
          className,
        )}
        aria-invalid={hasError || undefined}
        aria-describedby={describedBy}
        {...props}
      />
    );
  },
);

Input.displayName = 'Input';
