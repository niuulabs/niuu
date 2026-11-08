import type { ReactNode } from 'react';

/** One choice of a radio group, as a bordered card with a title and a line of explanation. */
export function RadioCard({
  name,
  value,
  checked,
  onChange,
  title,
  disabled = false,
  children,
}: {
  name: string;
  value: string;
  checked: boolean;
  onChange: () => void;
  title: string;
  disabled?: boolean;
  children?: ReactNode;
}) {
  return (
    <label
      className={`niuu:flex niuu:gap-2.5 niuu:rounded-lg niuu:border niuu:px-3 niuu:py-2 ${
        checked ? 'niuu:border-brand/35 niuu:bg-brand/15' : 'niuu:border-border'
      } ${disabled ? 'niuu:cursor-not-allowed niuu:opacity-50' : 'niuu:cursor-pointer'}`}
    >
      <input
        type="radio"
        name={name}
        value={value}
        checked={checked}
        disabled={disabled}
        onChange={onChange}
        className="niuu:mt-1"
      />
      <span>
        <span className="niuu:block niuu:text-sm niuu:text-text-primary">{title}</span>
        {children ? (
          <small className="niuu:block niuu:text-xs niuu:text-text-muted">{children}</small>
        ) : null}
      </span>
    </label>
  );
}
