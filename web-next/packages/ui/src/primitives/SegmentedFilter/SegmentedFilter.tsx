import { cn } from '../../utils/cn';

export interface SegmentedFilterOption<T extends string = string> {
  value: T;
  label: string;
  count?: number;
  disabled?: boolean;
}

export interface SegmentedFilterProps<T extends string = string> {
  options: SegmentedFilterOption<T>[];
  value: T;
  onChange: (value: T) => void;
  /** Accessible group label. */
  'aria-label'?: string;
  className?: string;
}

/**
 * A pill-based segmented control for filtering lists.
 * Each segment shows a label and optional count badge.
 */
export function SegmentedFilter<T extends string = string>({
  options,
  value,
  onChange,
  'aria-label': ariaLabel = 'Filter',
  className,
}: SegmentedFilterProps<T>) {
  return (
    <div
      className={cn(
        'niuu:flex niuu:gap-1 niuu:p-1 niuu:rounded-md niuu:bg-bg-tertiary niuu:w-fit',
        className,
      )}
      role="group"
      aria-label={ariaLabel}
    >
      {options.map((opt) => (
        <button
          key={opt.value}
          type="button"
          onClick={() => {
            if (!opt.disabled) onChange(opt.value);
          }}
          aria-pressed={value === opt.value}
          disabled={opt.disabled}
          className={cn(
            'niuu:rounded niuu:font-medium niuu:transition-colors niuu:px-3.5 niuu:py-1.5 niuu:text-xs',
            value === opt.value
              ? 'niuu:bg-bg-elevated niuu:text-text-primary'
              : 'niuu:text-text-muted niuu:hover:text-text-secondary',
            opt.disabled && 'niuu:cursor-not-allowed niuu:opacity-45 niuu:hover:text-text-muted',
          )}
        >
          {opt.label}
          {opt.count != null && <span className="niuu:ml-1.5 niuu:opacity-60">{opt.count}</span>}
        </button>
      ))}
    </div>
  );
}
