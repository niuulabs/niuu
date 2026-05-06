import { cn } from '../../utils/cn';
import './FilterBar.css';

export interface FilterChipProps {
  label: string;
  value: string;
  onRemove: () => void;
  className?: string;
}

export function FilterChip({ label, value, onRemove, className }: FilterChipProps) {
  return (
    <span className={cn('niuu-filter-chip', className)}>
      <span className="niuu-filter-chip__label">{label}</span>
      <span className="niuu-filter-chip__sep">:</span>
      <span className="niuu-filter-chip__value">{value}</span>
      <button
        type="button"
        className="niuu-filter-chip__remove"
        onClick={onRemove}
        aria-label={`Remove filter ${label}`}
      >
        ×
      </button>
    </span>
  );
}
