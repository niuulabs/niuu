import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Table, type TableColumn } from './Table';

interface Row {
  id: number;
  name: string;
  status: string;
}

const ROWS: Row[] = [
  { id: 1, name: 'Alice', status: 'active' },
  { id: 2, name: 'Bob', status: 'inactive' },
  { id: 3, name: 'Carol', status: 'active' },
];

const COLUMNS: TableColumn<Row>[] = [
  { key: 'name', header: 'Name', render: (r) => r.name, sortable: true },
  { key: 'status', header: 'Status', render: (r) => r.status },
];

describe('Table — rendering', () => {
  it('renders column headers', () => {
    render(<Table columns={COLUMNS} rows={ROWS} />);
    expect(screen.getByText('Name')).toBeInTheDocument();
    expect(screen.getByText('Status')).toBeInTheDocument();
  });

  it('renders all row data', () => {
    render(<Table columns={COLUMNS} rows={ROWS} />);
    expect(screen.getByText('Alice')).toBeInTheDocument();
    expect(screen.getByText('Bob')).toBeInTheDocument();
    expect(screen.getByText('Carol')).toBeInTheDocument();
  });

  it('renders empty table with no rows', () => {
    render(<Table columns={COLUMNS} rows={[]} />);
    expect(screen.queryByText('Alice')).not.toBeInTheDocument();
  });

  it('applies aria-label', () => {
    render(<Table columns={COLUMNS} rows={ROWS} aria-label="Users" />);
    expect(screen.getByRole('table', { name: 'Users' })).toBeInTheDocument();
  });

  it('applies custom className to wrapper', () => {
    const { container } = render(<Table columns={COLUMNS} rows={[]} className="custom" />);
    expect(container.firstChild).toHaveClass('custom');
  });

  it('applies column width via style', () => {
    const cols: TableColumn<Row>[] = [
      { key: 'name', header: 'Name', render: (r) => r.name, width: '200px' },
    ];
    render(<Table columns={cols} rows={ROWS} />);
    const th = screen.getByText('Name').closest('th');
    expect(th).toHaveStyle({ width: '200px' });
  });
});

describe('Table — sorting', () => {
  it('shows sort icon on sortable column', () => {
    const { container } = render(<Table columns={COLUMNS} rows={ROWS} />);
    const sortIcons = container.querySelectorAll('.niuu-table-sort-icon');
    expect(sortIcons.length).toBeGreaterThan(0);
  });

  it('marks sort icon as active for the sorted column', () => {
    const { container } = render(<Table columns={COLUMNS} rows={ROWS} sortKey="name" />);
    expect(container.querySelector('.niuu-table-sort-icon--active')).toBeInTheDocument();
  });

  it('applies descending class when sortDir=desc', () => {
    const { container } = render(
      <Table columns={COLUMNS} rows={ROWS} sortKey="name" sortDir="desc" />,
    );
    expect(container.querySelector('.niuu-table-sort-icon--desc')).toBeInTheDocument();
  });

  it('calls onSort with asc when clicking unsorted column', async () => {
    const onSort = vi.fn();
    render(<Table columns={COLUMNS} rows={ROWS} onSort={onSort} />);
    await userEvent.click(screen.getByText('Name'));
    expect(onSort).toHaveBeenCalledWith('name', 'asc');
  });

  it('toggles to desc when clicking already-asc-sorted column', async () => {
    const onSort = vi.fn();
    render(<Table columns={COLUMNS} rows={ROWS} sortKey="name" sortDir="asc" onSort={onSort} />);
    await userEvent.click(screen.getByText('Name'));
    expect(onSort).toHaveBeenCalledWith('name', 'desc');
  });

  it('toggles to asc when clicking already-desc-sorted column', async () => {
    const onSort = vi.fn();
    render(<Table columns={COLUMNS} rows={ROWS} sortKey="name" sortDir="desc" onSort={onSort} />);
    await userEvent.click(screen.getByText('Name'));
    expect(onSort).toHaveBeenCalledWith('name', 'asc');
  });

  it('does not call onSort when clicking non-sortable column', async () => {
    const onSort = vi.fn();
    render(<Table columns={COLUMNS} rows={ROWS} onSort={onSort} />);
    await userEvent.click(screen.getByText('Status'));
    expect(onSort).not.toHaveBeenCalled();
  });

  it('has aria-sort=ascending on sorted column', () => {
    render(<Table columns={COLUMNS} rows={ROWS} sortKey="name" sortDir="asc" />);
    const th = screen.getByText('Name').closest('th');
    expect(th).toHaveAttribute('aria-sort', 'ascending');
  });

  it('has aria-sort=descending on desc-sorted column', () => {
    render(<Table columns={COLUMNS} rows={ROWS} sortKey="name" sortDir="desc" />);
    const th = screen.getByText('Name').closest('th');
    expect(th).toHaveAttribute('aria-sort', 'descending');
  });

  it('has aria-sort=none on unsorted sortable column', () => {
    render(<Table columns={COLUMNS} rows={ROWS} />);
    const th = screen.getByText('Name').closest('th');
    expect(th).toHaveAttribute('aria-sort', 'none');
  });
});

describe('Table — row selection', () => {
  it('renders select-all checkbox in header', () => {
    render(
      <Table columns={COLUMNS} rows={ROWS} selectedIds={new Set()} onSelectionChange={() => {}} />,
    );
    expect(screen.getByRole('checkbox', { name: 'select all' })).toBeInTheDocument();
  });

  it('renders per-row checkboxes', () => {
    render(
      <Table columns={COLUMNS} rows={ROWS} selectedIds={new Set()} onSelectionChange={() => {}} />,
    );
    expect(screen.getAllByRole('checkbox')).toHaveLength(4); // 1 header + 3 rows
  });

  it('does not render selection column without selectedIds', () => {
    render(<Table columns={COLUMNS} rows={ROWS} />);
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
  });

  it('row checkbox reflects selected state', () => {
    render(
      <Table
        columns={COLUMNS}
        rows={ROWS}
        selectedIds={new Set([1])}
        onSelectionChange={() => {}}
      />,
    );
    expect(screen.getByRole('checkbox', { name: 'select row 1' })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: 'select row 2' })).not.toBeChecked();
  });

  it('selects a row when its checkbox is clicked', async () => {
    const onSelectionChange = vi.fn();
    render(
      <Table
        columns={COLUMNS}
        rows={ROWS}
        selectedIds={new Set()}
        onSelectionChange={onSelectionChange}
      />,
    );
    await userEvent.click(screen.getByRole('checkbox', { name: 'select row 1' }));
    expect(onSelectionChange).toHaveBeenCalledWith(new Set([1]));
  });

  it('deselects a row when its checkbox is clicked while selected', async () => {
    const onSelectionChange = vi.fn();
    render(
      <Table
        columns={COLUMNS}
        rows={ROWS}
        selectedIds={new Set([1])}
        onSelectionChange={onSelectionChange}
      />,
    );
    await userEvent.click(screen.getByRole('checkbox', { name: 'select row 1' }));
    expect(onSelectionChange).toHaveBeenCalledWith(new Set());
  });

  it('selects all rows when select-all is clicked with none selected', async () => {
    const onSelectionChange = vi.fn();
    render(
      <Table
        columns={COLUMNS}
        rows={ROWS}
        selectedIds={new Set()}
        onSelectionChange={onSelectionChange}
      />,
    );
    await userEvent.click(screen.getByRole('checkbox', { name: 'select all' }));
    expect(onSelectionChange).toHaveBeenCalledWith(new Set([1, 2, 3]));
  });

  it('deselects all rows when select-all is clicked with all selected', async () => {
    const onSelectionChange = vi.fn();
    render(
      <Table
        columns={COLUMNS}
        rows={ROWS}
        selectedIds={new Set([1, 2, 3])}
        onSelectionChange={onSelectionChange}
      />,
    );
    await userEvent.click(screen.getByRole('checkbox', { name: 'select all' }));
    expect(onSelectionChange).toHaveBeenCalledWith(new Set());
  });

  it('applies selected class to selected rows', () => {
    const { container } = render(
      <Table
        columns={COLUMNS}
        rows={ROWS}
        selectedIds={new Set([2])}
        onSelectionChange={() => {}}
      />,
    );
    const rows = container.querySelectorAll('.niuu-table-row');
    // First data row (id=1) is not selected
    expect(rows[0]).not.toHaveClass('niuu-table-row--selected');
    // Second data row (id=2) is selected
    expect(rows[1]).toHaveClass('niuu-table-row--selected');
  });
});

describe('Table — row expand', () => {
  const getExpandedContent = (row: Row) => <div>Details for {row.name}</div>;

  it('renders expand buttons', () => {
    render(
      <Table
        columns={COLUMNS}
        rows={ROWS}
        expandedId={null}
        onExpandChange={() => {}}
        getExpandedContent={getExpandedContent}
      />,
    );
    const btns = screen.getAllByRole('button', { name: 'expand row' });
    expect(btns).toHaveLength(3);
  });

  it('does not render expand column without getExpandedContent', () => {
    render(<Table columns={COLUMNS} rows={ROWS} />);
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  it('calls onExpandChange with row id when expand button is clicked', async () => {
    const onExpandChange = vi.fn();
    render(
      <Table
        columns={COLUMNS}
        rows={ROWS}
        expandedId={null}
        onExpandChange={onExpandChange}
        getExpandedContent={getExpandedContent}
      />,
    );
    await userEvent.click(screen.getAllByRole('button', { name: 'expand row' })[0]);
    expect(onExpandChange).toHaveBeenCalledWith(1);
  });

  it('calls onExpandChange with null when expanded row is clicked again', async () => {
    const onExpandChange = vi.fn();
    render(
      <Table
        columns={COLUMNS}
        rows={ROWS}
        expandedId={1}
        onExpandChange={onExpandChange}
        getExpandedContent={getExpandedContent}
      />,
    );
    await userEvent.click(screen.getByRole('button', { name: 'collapse row' }));
    expect(onExpandChange).toHaveBeenCalledWith(null);
  });

  it('shows expanded content for expanded row', () => {
    render(
      <Table
        columns={COLUMNS}
        rows={ROWS}
        expandedId={1}
        onExpandChange={() => {}}
        getExpandedContent={getExpandedContent}
      />,
    );
    expect(screen.getByText('Details for Alice')).toBeInTheDocument();
  });

  it('does not show expanded content for non-expanded rows', () => {
    render(
      <Table
        columns={COLUMNS}
        rows={ROWS}
        expandedId={1}
        onExpandChange={() => {}}
        getExpandedContent={getExpandedContent}
      />,
    );
    expect(screen.queryByText('Details for Bob')).not.toBeInTheDocument();
  });

  it('collapse button has aria-expanded=true', () => {
    render(
      <Table
        columns={COLUMNS}
        rows={ROWS}
        expandedId={1}
        onExpandChange={() => {}}
        getExpandedContent={getExpandedContent}
      />,
    );
    expect(screen.getByRole('button', { name: 'collapse row' })).toHaveAttribute(
      'aria-expanded',
      'true',
    );
  });

  it('applies open class to expanded row expand button', () => {
    const { container } = render(
      <Table
        columns={COLUMNS}
        rows={ROWS}
        expandedId={1}
        onExpandChange={() => {}}
        getExpandedContent={getExpandedContent}
      />,
    );
    const openBtn = container.querySelector('.niuu-table-expand-btn--open');
    expect(openBtn).toBeInTheDocument();
  });
});

describe('Table — combined selection + expand', () => {
  const getExpandedContent = (row: Row) => <div>expanded {row.id}</div>;

  it('renders both selection and expand columns', () => {
    render(
      <Table
        columns={COLUMNS}
        rows={ROWS}
        selectedIds={new Set()}
        onSelectionChange={() => {}}
        expandedId={null}
        onExpandChange={() => {}}
        getExpandedContent={getExpandedContent}
      />,
    );
    expect(screen.getByRole('checkbox', { name: 'select all' })).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: 'expand row' })).toHaveLength(3);
  });

  it('expanded content spans all columns including selection and expand', () => {
    const { container } = render(
      <Table
        columns={COLUMNS}
        rows={ROWS}
        selectedIds={new Set()}
        onSelectionChange={() => {}}
        expandedId={1}
        onExpandChange={() => {}}
        getExpandedContent={getExpandedContent}
      />,
    );
    const expandContentTd = container.querySelector('.niuu-table-td--expand-content');
    // 2 data cols + 1 select col + 1 expand col = 4
    expect(expandContentTd).toHaveAttribute('colspan', '4');
  });
});

describe('Table — string IDs', () => {
  it('works with string IDs', async () => {
    const strRows = [{ id: 'a', name: 'Alpha', status: 'on' }];
    const onSelectionChange = vi.fn();
    render(
      <Table
        columns={COLUMNS as TableColumn<{ id: string; name: string; status: string }>[]}
        rows={strRows}
        selectedIds={new Set()}
        onSelectionChange={onSelectionChange}
      />,
    );
    await userEvent.click(screen.getByRole('checkbox', { name: 'select row a' }));
    expect(onSelectionChange).toHaveBeenCalledWith(new Set(['a']));
  });
});
