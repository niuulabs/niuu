import { forwardRef, type TextareaHTMLAttributes } from 'react';
import { cn } from '../../utils/cn';
import { useField } from '../Field';

export interface TextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  rows?: number;
}

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ className, id: idProp, rows = 4, 'aria-describedby': ariaDescribedBy, ...props }, ref) => {
    const { id: fieldId, hintId, errorId, hasError } = useField();
    const id = idProp ?? fieldId;

    const describedBy = [hintId, errorId, ariaDescribedBy].filter(Boolean).join(' ') || undefined;

    return (
      <textarea
        ref={ref}
        id={id}
        rows={rows}
        className={cn(
          'niuu-form-control niuu-textarea',
          hasError && 'niuu-form-control--error niuu-textarea--error',
          className,
        )}
        aria-invalid={hasError || undefined}
        aria-describedby={describedBy}
        {...props}
      />
    );
  },
);

Textarea.displayName = 'Textarea';
