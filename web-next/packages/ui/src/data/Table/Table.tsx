import { Fragment, type ReactNode } from 'react';
import { cn } from '../../utils/cn';
import './Table.css';

export type SortDir = 'asc' | 'desc';

export interface TableColumn<T> {
  key: string;
  header: ReactNode;
  render: (row: T) => ReactNode;
  sortable?: boolean;
  width?: string;
}

export interface TableProps<T extends { id: string | number }> {
  columns: TableColumn<T>[];
  rows: T[];
  /** Key of the currently sorted column. */
  sortKey?: string | null;
  sortDir?: SortDir;
  /** Called when a sortable header is clicked. */
  onSort?: (key: string, dir: SortDir) => void;
  /** Set of selected row IDs (enables selection column). */
  selectedIds?: Set<string | number>;
  /** Called when selection changes. */
  onSelectionChange?: (ids: Set<string | number>) => void;
  /** ID of the currently expanded row. */
  expandedId?: string | number | null;
  /** Called when a row's expand state changes. */
  onExpandChange?: (id: string | number | null) => void;
  /** Returns expanded content for a row (enables expand column). */
  getExpandedContent?: (row: T) => ReactNode;
  className?: string;
  'aria-label'?: string;
}

export function Table<T extends { id: string | number }>({
  columns,
  rows,
  sortKey = null,
  sortDir = 'asc',
  onSort,
  selectedIds,
  onSelectionChange,
  expandedId = null,
  onExpandChange,
  getExpandedContent,
  className,
  'aria-label': ariaLabel,
}: TableProps<T>) {
  const hasSelection = selectedIds !== undefined && onSelectionChange !== undefined;
  const hasExpand = getExpandedContent !== undefined && onExpandChange !== undefined;

  const allSelected = rows.length > 0 && rows.every((r) => selectedIds?.has(r.id));
  const someSelected = rows.some((r) => selectedIds?.has(r.id));
  const colSpan = columns.length + (hasExpand ? 1 : 0) + (hasSelection ? 1 : 0);

  function handleSort(key: string) {
    if (!onSort) return;
    if (sortKey === key) {
      onSort(key, sortDir === 'asc' ? 'desc' : 'asc');
    } else {
      onSort(key, 'asc');
    }
  }

  function handleSelectAll() {
    if (!onSelectionChange) return;
    if (allSelected) {
      onSelectionChange(new Set());
    } else {
      onSelectionChange(new Set(rows.map((r) => r.id)));
    }
  }

  function handleSelectRow(id: string | number) {
    if (!onSelectionChange || !selectedIds) return;
    const next = new Set(selectedIds);
    if (next.has(id)) {
      next.delete(id);
    } else {
      next.add(id);
    }
    onSelectionChange(next);
  }

  function handleExpandRow(id: string | number) {
    if (!onExpandChange) return;
    onExpandChange(expandedId === id ? null : id);
  }

  function sortAriaLabel(key: string): 'ascending' | 'descending' | 'none' | undefined {
    if (sortKey === key) {
      return sortDir === 'asc' ? 'ascending' : 'descending';
    }
    return columns.find((c) => c.key === key)?.sortable ? 'none' : undefined;
  }

  return (
    <div className={cn('niuu-table-wrapper', className)}>
      <table className="niuu-table" aria-label={ariaLabel}>
        <thead className="niuu-table-head">
          <tr>
            {hasExpand && (
              <th className="niuu-table-th niuu-table-th--expand" aria-label="expand" />
            )}
            {hasSelection && (
              <th className="niuu-table-th niuu-table-th--select">
                <input
                  type="checkbox"
                  aria-label="select all"
                  checked={allSelected}
                  ref={(el: HTMLInputElement | null) => {
                    if (el) el.indeterminate = someSelected && !allSelected;
                  }}
                  onChange={handleSelectAll}
                />
              </th>
            )}
            {columns.map((col) => (
              <th
                key={col.key}
                className={cn('niuu-table-th', col.sortable && 'niuu-table-th--sortable')}
                style={col.width ? { width: col.width } : undefined}
                onClick={col.sortable ? () => handleSort(col.key) : undefined}
                aria-sort={sortAriaLabel(col.key)}
              >
                <span className="niuu-table-th-inner">
                  {col.header}
                  {col.sortable && (
                    <span
                      className={cn(
                        'niuu-table-sort-icon',
                        sortKey === col.key && 'niuu-table-sort-icon--active',
                        sortKey === col.key && sortDir === 'desc' && 'niuu-table-sort-icon--desc',
                      )}
                      aria-hidden="true"
                    >
                      ▲
                    </span>
                  )}
                </span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="niuu-table-body">
          {rows.map((row) => (
            <Fragment key={row.id}>
              <tr
                className={cn(
                  'niuu-table-row',
                  selectedIds?.has(row.id) && 'niuu-table-row--selected',
                  expandedId === row.id && 'niuu-table-row--expanded',
                )}
              >
                {hasExpand && (
                  <td className="niuu-table-td niuu-table-td--expand">
                    <button
                      type="button"
                      className={cn(
                        'niuu-table-expand-btn',
                        expandedId === row.id && 'niuu-table-expand-btn--open',
                      )}
                      onClick={() => handleExpandRow(row.id)}
                      aria-label={expandedId === row.id ? 'collapse row' : 'expand row'}
                      aria-expanded={expandedId === row.id}
                    >
                      ▶
                    </button>
                  </td>
                )}
                {hasSelection && (
                  <td className="niuu-table-td niuu-table-td--select">
                    <input
                      type="checkbox"
                      aria-label={`select row ${row.id}`}
                      checked={selectedIds?.has(row.id) ?? false}
                      onChange={() => handleSelectRow(row.id)}
                    />
                  </td>
                )}
                {columns.map((col) => (
                  <td key={col.key} className="niuu-table-td">
                    {col.render(row)}
                  </td>
                ))}
              </tr>
              {hasExpand && expandedId === row.id && (
                <tr className="niuu-table-row niuu-table-row--expand-content">
                  <td colSpan={colSpan} className="niuu-table-td niuu-table-td--expand-content">
                    {getExpandedContent(row)}
                  </td>
                </tr>
              )}
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
  );
}
