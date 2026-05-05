import { type ReactNode, type ChangeEvent } from 'react';
import { cn } from '../../utils/cn';
import { FilterChip } from './FilterChip';
import './FilterBar.css';

/**
 * Typed filter state: string values map directly to URL search params when used
 * with TanStack Router's `useSearch` + `useNavigate`.
 *
 * @example
 * // In a plugin with TanStack Router:
 * const search = useSearch({ from: myRoute.id });
 * const navigate = useNavigate({ from: myRoute.id });
 * const filters: FilterState = { q: search.q ?? '', status: search.status ?? '' };
 * const onChange = (next: FilterState) => navigate({ search: (prev) => ({ ...prev, ...next }) });
 */
export type FilterState = Record<string, string>;

export interface ActiveFilter {
  key: string;
  label: string;
  value: string;
}

export interface FilterBarProps {
  /** Current filter state (controlled). */
  value: FilterState;
  onChange: (next: FilterState) => void;
  /** Which key in FilterState is the free-text search field (default: 'q'). */
  searchKey?: string;
  /** Placeholder text for the search input. */
  placeholder?: string;
  /** Active filter chips derived from value (omit to derive automatically). */
  activeFilters?: ActiveFilter[];
  /** Extra controls (toggles, dropdowns) to render after the search input. */
  children?: ReactNode;
  className?: string;
}

export function FilterBar({
  value,
  onChange,
  searchKey = 'q',
  placeholder = 'Search…',
  activeFilters,
  children,
  className,
}: FilterBarProps) {
  const searchValue = value[searchKey] ?? '';

  const chips: ActiveFilter[] =
    activeFilters ??
    Object.entries(value)
      .filter(([k, v]) => k !== searchKey && v !== '' && v !== undefined)
      .map(([k, v]) => ({ key: k, label: k, value: v }));

  function handleSearchChange(e: ChangeEvent<HTMLInputElement>) {
    onChange({ ...value, [searchKey]: e.target.value });
  }

  function handleRemoveChip(key: string) {
    const next = { ...value };
    delete next[key];
    onChange(next);
  }

  function handleClearAll() {
    onChange({});
  }

  const hasActive = chips.length > 0 || searchValue !== '';

  return (
    <div className={cn('niuu-filter-bar', className)}>
      <div className="niuu-filter-bar__search-row">
        <input
          type="search"
          className="niuu-filter-bar__input"
          value={searchValue}
          onChange={handleSearchChange}
          placeholder={placeholder}
          aria-label="Search"
        />
        {children && <div className="niuu-filter-bar__controls">{children}</div>}
        {hasActive && (
          <button
            type="button"
            className="niuu-filter-bar__clear"
            onClick={handleClearAll}
            aria-label="Clear all filters"
          >
            Clear
          </button>
        )}
      </div>
      {chips.length > 0 && (
        <div className="niuu-filter-bar__chips" role="group" aria-label="Active filters">
          {chips.map((f) => (
            <FilterChip
              key={f.key}
              label={f.label}
              value={f.value}
              onRemove={() => handleRemoveChip(f.key)}
            />
          ))}
        </div>
      )}
    </div>
  );
}
