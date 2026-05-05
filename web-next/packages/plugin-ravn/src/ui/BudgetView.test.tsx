import { describe, it, expect } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { BudgetView } from './BudgetView';
import { createMockBudgetStream, createMockRavenStream } from '../adapters/mock';
import { wrapWithServices } from '../testing/wrapWithRavn';

const wrap = wrapWithServices;

const services = {
  'ravn.budget': createMockBudgetStream(),
  'ravn.ravens': createMockRavenStream(),
};

describe('BudgetView', () => {
  it('shows loading state initially', () => {
    render(<BudgetView />, { wrapper: wrap(services) });
    expect(screen.getByText(/loading budget/i)).toBeInTheDocument();
  });

  it('shows hero card after loading', async () => {
    render(<BudgetView />, { wrapper: wrap(services) });
    await waitFor(() => expect(screen.getByLabelText(/fleet budget/i)).toBeInTheDocument());
  });

  it('shows elapsed time header in hero', async () => {
    render(<BudgetView />, { wrapper: wrap(services) });
    await waitFor(() => {
      expect(screen.getByText(/18H OF 24H ELAPSED/)).toBeInTheDocument();
    });
  });

  it('shows large spent value and cap in hero', async () => {
    render(<BudgetView />, { wrapper: wrap(services) });
    await waitFor(() => {
      expect(screen.getByText(/spent of/)).toBeInTheDocument();
    });
  });

  it('shows runway bar with projection pill', async () => {
    render(<BudgetView />, { wrapper: wrap(services) });
    await waitFor(() => {
      expect(screen.getByTestId('runway-bar')).toBeInTheDocument();
      expect(screen.getByText(/projecting/i)).toBeInTheDocument();
      expect(screen.getByText(/headroom/i)).toBeInTheDocument();
    });
  });

  it('renders four attention columns', async () => {
    render(<BudgetView />, { wrapper: wrap(services) });
    await waitFor(() => {
      expect(screen.getByLabelText(/budget attention/i)).toBeInTheDocument();
      expect(screen.getByLabelText('Over cap')).toBeInTheDocument();
      expect(screen.getByLabelText('Will exceed cap by EOD')).toBeInTheDocument();
      expect(screen.getByLabelText('Near cap (≥70%)')).toBeInTheDocument();
      expect(screen.getByLabelText('Accelerating')).toBeInTheDocument();
    });
  });

  it('shows fleet sparkline chart after loading', async () => {
    render(<BudgetView />, { wrapper: wrap(services) });
    await waitFor(() => {
      expect(screen.getByTestId('fleet-sparkline')).toBeInTheDocument();
    });
  });

  it('fleet burn has correct title', async () => {
    render(<BudgetView />, { wrapper: wrap(services) });
    await waitFor(() => {
      expect(screen.getByText(/Fleet burn/i)).toBeInTheDocument();
    });
  });

  it('collapsible fleet table is hidden initially', async () => {
    render(<BudgetView />, { wrapper: wrap(services) });
    await waitFor(() => expect(screen.getByText(/full fleet table/i)).toBeInTheDocument());
    expect(screen.queryByLabelText(/fleet budget table/i)).not.toBeInTheDocument();
  });

  it('expands fleet table when toggle is clicked', async () => {
    render(<BudgetView />, { wrapper: wrap(services) });
    await waitFor(() => expect(screen.getByText(/full fleet table/i)).toBeInTheDocument());
    fireEvent.click(screen.getByText(/full fleet table/i));
    expect(screen.getByLabelText(/fleet budget table/i)).toBeInTheDocument();
  });

  it('collapses fleet table on second click', async () => {
    render(<BudgetView />, { wrapper: wrap(services) });
    await waitFor(() => expect(screen.getByText(/full fleet table/i)).toBeInTheDocument());
    fireEvent.click(screen.getByText(/full fleet table/i));
    expect(screen.getByLabelText(/fleet budget table/i)).toBeInTheDocument();
    fireEvent.click(screen.getByText(/full fleet table/i));
    expect(screen.queryByLabelText(/fleet budget table/i)).not.toBeInTheDocument();
  });

  it('fleet table has correct column headers', async () => {
    render(<BudgetView />, { wrapper: wrap(services) });
    await waitFor(() => expect(screen.getByText(/full fleet table/i)).toBeInTheDocument());
    fireEvent.click(screen.getByText(/full fleet table/i));
    const table = screen.getByLabelText(/fleet budget table/i);
    expect(table).toBeInTheDocument();
    expect(table.querySelector('th:first-child')).toHaveTextContent('Raven');
    const headers = table.querySelectorAll('th');
    const headerTexts = Array.from(headers).map((h) => h.textContent);
    expect(headerTexts).toContain('Spent');
    expect(headerTexts).toContain('Cap');
  });

  it('shows top drivers section after budgets load', async () => {
    render(<BudgetView />, { wrapper: wrap(services) });
    await waitFor(() => expect(screen.getByTestId('top-drivers')).toBeInTheDocument(), {
      timeout: 3000,
    });
  });

  it('top drivers has correct header', async () => {
    render(<BudgetView />, { wrapper: wrap(services) });
    await waitFor(() => expect(screen.getByText('Top drivers today')).toBeInTheDocument(), {
      timeout: 3000,
    });
    expect(screen.getByText('ravens ranked by absolute $ spent')).toBeInTheDocument();
  });

  it('top drivers list has driver rows', async () => {
    render(<BudgetView />, { wrapper: wrap(services) });
    await waitFor(() => expect(screen.getAllByTestId('driver-row').length).toBeGreaterThan(0), {
      timeout: 3000,
    });
  });

  it('top driver rows contain sparklines', async () => {
    render(<BudgetView />, { wrapper: wrap(services) });
    await waitFor(
      () => {
        const rows = screen.getAllByTestId('driver-row');
        expect(rows.length).toBeGreaterThan(0);
        const firstRow = rows[0]!;
        expect(firstRow.querySelector('.niuu-sparkline')).toBeTruthy();
      },
      { timeout: 3000 },
    );
  });

  it('shows recommended changes when ravens need attention', async () => {
    render(<BudgetView />, { wrapper: wrap(services) });
    await waitFor(() => expect(screen.getByTestId('recommended-changes')).toBeInTheDocument(), {
      timeout: 3000,
    });
  });

  it('recommendation rows show action buttons', async () => {
    render(<BudgetView />, { wrapper: wrap(services) });
    await waitFor(
      () => {
        const buttons = screen.getAllByTestId('rec-action');
        expect(buttons.length).toBeGreaterThan(0);
      },
      { timeout: 3000 },
    );
  });

  it('recommendation rows show attention badges', async () => {
    render(<BudgetView />, { wrapper: wrap(services) });
    await waitFor(
      () => {
        const badges = document.querySelectorAll('[aria-label^="attention:"]');
        expect(badges.length).toBeGreaterThan(0);
      },
      { timeout: 3000 },
    );
  });
});
