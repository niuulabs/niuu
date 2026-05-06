import { cn } from '../../utils/cn';
import './FilterBar.css';

export interface FilterToggleProps {
  label: string;
  active: boolean;
  onChange: (active: boolean) => void;
  className?: string;
}

export function FilterToggle({ label, active, onChange, className }: FilterToggleProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={active}
      className={cn('niuu-filter-toggle', active && 'niuu-filter-toggle--active', className)}
      onClick={() => onChange(!active)}
    >
      {label}
    </button>
  );
}
