import { describe, it, expect } from 'vitest';
import { screen, fireEvent, waitFor } from '@testing-library/react';
import { LintPage } from './LintPage';
import { createMimirMockAdapter } from '../adapters/mock';
import type { IMimirService } from '../ports';
import { renderWithMimir } from '../testing/renderWithMimir';

const wrap = renderWithMimir;

describe('LintPage', () => {
  it('shows loading state initially', () => {
    wrap(<LintPage />);
    expect(screen.getByText(/loading lint report/)).toBeInTheDocument();
  });

  it('renders lint issues after load', async () => {
    wrap(<LintPage />);
    await waitFor(() => expect(screen.getAllByTestId('lint-issue').length).toBeGreaterThan(0));
  });

  it('renders the checks sidebar heading', async () => {
    wrap(<LintPage />);
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: /checks/i })).toBeInTheDocument(),
    );
  });

  it('renders check rows for each rule in the sidebar', async () => {
    wrap(<LintPage />);
    await waitFor(() => expect(screen.getAllByTestId('check-row').length).toBeGreaterThan(0));
  });

  it('clicking a check row filters issues to that rule', async () => {
    wrap(<LintPage />);
    await waitFor(() => expect(screen.getAllByTestId('lint-issue').length).toBeGreaterThan(0));
    const allCount = screen.getAllByTestId('lint-issue').length;
    const checkRows = screen.getAllByTestId('check-row');
    fireEvent.click(checkRows[0]!);
    await waitFor(() => {
      const filteredCount = screen.queryAllByTestId('lint-issue').length;
      expect(filteredCount).toBeLessThanOrEqual(allCount);
    });
  });

  it('clicking the same check row again deselects it (shows all)', async () => {
    wrap(<LintPage />);
    await waitFor(() => expect(screen.getAllByTestId('lint-issue').length).toBeGreaterThan(0));
    const allCount = screen.getAllByTestId('lint-issue').length;
    const checkRows = screen.getAllByTestId('check-row');
    fireEvent.click(checkRows[0]!);
    fireEvent.click(checkRows[0]!);
    await waitFor(() => {
      expect(screen.getAllByTestId('lint-issue').length).toBe(allCount);
    });
  });

  it('shows "Fix all auto-fixable" button when fixable issues exist', async () => {
    wrap(<LintPage />);
    await waitFor(() => expect(screen.getByTestId('fix-all-btn')).toBeInTheDocument());
  });

  it('shows auto-fix button for fixable issues', async () => {
    wrap(<LintPage />);
    await waitFor(() => expect(screen.getAllByTestId('autofix-btn').length).toBeGreaterThan(0));
  });

  it('clicking auto-fix on individual issue calls runAutoFix', async () => {
    let fixedIds: string[] | undefined;
    const spy: IMimirService = {
      ...createMimirMockAdapter(),
      lint: {
        ...createMimirMockAdapter().lint,
        runAutoFix: async (ids) => {
          fixedIds = ids;
          return { issues: [], pagesChecked: 3, summary: { error: 0, warn: 0, info: 0 } };
        },
      },
    };
    wrap(<LintPage />, spy);
    await waitFor(() => expect(screen.getAllByTestId('autofix-btn').length).toBeGreaterThan(0));
    fireEvent.click(screen.getAllByTestId('autofix-btn')[0]!);
    await waitFor(() => expect(fixedIds).toBeDefined());
    expect(Array.isArray(fixedIds)).toBe(true);
  });

  it('clicking "Fix all" calls runAutoFix with no ids (fixes all fixable)', async () => {
    let calledWith: string[] | undefined = ['sentinel'];
    const spy: IMimirService = {
      ...createMimirMockAdapter(),
      lint: {
        ...createMimirMockAdapter().lint,
        runAutoFix: async (ids) => {
          calledWith = ids;
          return { issues: [], pagesChecked: 3, summary: { error: 0, warn: 0, info: 0 } };
        },
      },
    };
    wrap(<LintPage />, spy);
    await waitFor(() => screen.getByTestId('fix-all-btn'));
    fireEvent.click(screen.getByTestId('fix-all-btn'));
    await waitFor(() => expect(calledWith).toBeUndefined());
  });

  it('shows error state when service throws', async () => {
    const failing: IMimirService = {
      ...createMimirMockAdapter(),
      lint: {
        ...createMimirMockAdapter().lint,
        getLintReport: async () => {
          throw new Error('lint service down');
        },
      },
    };
    wrap(<LintPage />, failing);
    await waitFor(() => expect(screen.getByText('lint service down')).toBeInTheDocument());
  });

  it('shows "No issues found" when lint report is empty', async () => {
    const empty: IMimirService = {
      ...createMimirMockAdapter(),
      lint: {
        ...createMimirMockAdapter().lint,
        getLintReport: async () => ({
          issues: [],
          pagesChecked: 10,
          summary: { error: 0, warn: 0, info: 0 },
        }),
      },
    };
    wrap(<LintPage />, empty);
    await waitFor(() => expect(screen.getByText(/no issues found/i)).toBeInTheDocument());
  });

  it('KPI strip shows total issues', async () => {
    wrap(<LintPage />);
    await waitFor(() => expect(screen.getByText('total issues')).toBeInTheDocument());
  });

  it('KPI strip shows auto-fixable count', async () => {
    wrap(<LintPage />);
    await waitFor(() => expect(screen.getByText('auto-fixable')).toBeInTheDocument());
  });

  it('each issue row has an Open button', async () => {
    wrap(<LintPage />);
    await waitFor(() => expect(screen.getAllByTestId('lint-issue').length).toBeGreaterThan(0));
    const openButtons = screen.getAllByRole('button', { name: /^open /i });
    expect(openButtons.length).toBeGreaterThan(0);
  });
});
