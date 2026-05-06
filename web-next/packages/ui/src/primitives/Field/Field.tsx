import { createContext, useContext, useId, type ReactNode } from 'react';
import { cn } from '../../utils/cn';

export interface FieldContextValue {
  id: string;
  hintId: string | undefined;
  errorId: string | undefined;
  hasError: boolean;
  required: boolean;
}

const FieldContext = createContext<FieldContextValue>({
  id: '',
  hintId: undefined,
  errorId: undefined,
  hasError: false,
  required: false,
});

export function useField(): FieldContextValue {
  return useContext(FieldContext);
}

export interface FieldProps {
  id?: string;
  label: string;
  hint?: string;
  error?: string;
  required?: boolean;
  className?: string;
  children: ReactNode;
}

export function Field({
  id: idProp,
  label,
  hint,
  error,
  required = false,
  className,
  children,
}: FieldProps) {
  const autoId = useId();
  const id = idProp ?? autoId;
  const hintId = hint ? `${id}-hint` : undefined;
  const errorId = error ? `${id}-error` : undefined;
  const hasError = Boolean(error);

  return (
    <FieldContext.Provider value={{ id, hintId, errorId, hasError, required }}>
      <div className={cn('niuu-field', hasError && 'niuu-field--error', className)}>
        <label htmlFor={id} className="niuu-field__label">
          {label}
          {required && (
            <span className="niuu-field__required" aria-hidden="true">
              {' '}
              *
            </span>
          )}
        </label>
        {hint && (
          <span id={hintId} className="niuu-field__hint">
            {hint}
          </span>
        )}
        {children}
        {error && (
          <span id={errorId} className="niuu-field__error" role="alert">
            {error}
          </span>
        )}
      </div>
    </FieldContext.Provider>
  );
}
