import type { ReactNode } from 'react';
import { cn } from '@/utils';
import styles from './FilterTabs.module.css';

export interface FilterTabsProps {
  /** Available filter options */
  options: string[];
  /** Currently selected value */
  value: string;
  /** Callback when selection changes */
  onChange: (value: string) => void;
  /** Custom renderer for option content; falls back to the option string */
  renderOption?: (option: string) => ReactNode;
  /** Additional CSS class */
  className?: string;
}

export function FilterTabs({ options, value, onChange, renderOption, className }: FilterTabsProps) {
  return (
    <div className={cn(styles.container, className)}>
      {options.map(option => (
        <button
          key={option}
          type="button"
          className={cn(styles.tab, value === option && styles.active)}
          onClick={() => onChange(option)}
        >
          {renderOption?.(option) ?? option}
        </button>
      ))}
    </div>
  );
}
