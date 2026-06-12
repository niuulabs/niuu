import { describe, it, expect, vi } from 'vitest';
import { screen, fireEvent, waitFor } from '@testing-library/react';
import { DoctorPage } from './DoctorPage';
import { createMimirMockAdapter } from '../adapters/mock';
import type { IMimirService } from '../ports';
import { renderWithMimir } from '../testing/renderWithMimir';

const wrap = renderWithMimir;

function withMounts(overrides: Partial<IMimirService['mounts']>): IMimirService {
  const base = createMimirMockAdapter();
  return { ...base, mounts: { ...base.mounts, ...overrides } };
}

describe('DoctorPage', () => {
  it('shows loading state initially', () => {
    wrap(<DoctorPage />);
    expect(screen.getByText(/running doctor checks/)).toBeInTheDocument();
  });

  it('renders the score and worst-status badge', async () => {
    wrap(<DoctorPage />);
    await waitFor(() => expect(screen.getByTestId('doctor-score')).toBeInTheDocument());
    expect(screen.getByTestId('doctor-score').textContent).toBe('3/6');
    expect(screen.getByTestId('doctor-worst').textContent).toBe('FAIL');
  });

  it('renders all checks with semantic status badges', async () => {
    wrap(<DoctorPage />);
    await waitFor(() => expect(screen.getAllByTestId('doctor-check')).toHaveLength(6));
    expect(screen.getAllByTestId('status-pass').length).toBeGreaterThan(0);
    expect(screen.getAllByTestId('status-warn').length).toBeGreaterThan(0);
    expect(screen.getAllByTestId('status-fail').length).toBeGreaterThan(0);
  });

  it('shows remediation for non-passing checks', async () => {
    wrap(<DoctorPage />);
    await waitFor(() => expect(screen.getAllByTestId('doctor-check').length).toBeGreaterThan(0));
    expect(
      screen.getByText(/re-run the embedding backfill for the platform mount/i),
    ).toBeInTheDocument();
  });

  it('marks fixable checks', async () => {
    wrap(<DoctorPage />);
    await waitFor(() => expect(screen.getAllByTestId('doctor-check').length).toBeGreaterThan(0));
    expect(screen.getAllByText('fixable').length).toBe(2);
  });

  it('shows the Run fixes button with the fixable count', async () => {
    wrap(<DoctorPage />);
    await waitFor(() => expect(screen.getByTestId('run-fixes-btn')).toBeInTheDocument());
    expect(screen.getByTestId('run-fixes-btn').textContent).toContain('Run fixes (2)');
  });

  it('opens a confirm dialog before running fixes', async () => {
    wrap(<DoctorPage />);
    await waitFor(() => expect(screen.getByTestId('run-fixes-btn')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('run-fixes-btn'));
    await waitFor(() => expect(screen.getByText(/run automatic fixes\?/i)).toBeInTheDocument());
    expect(screen.getByTestId('confirm-fix')).toBeInTheDocument();
  });

  it('cancelling the dialog does not run fixes', async () => {
    const runDoctorFixes = vi.fn();
    const base = createMimirMockAdapter();
    const service = withMounts({
      getDoctor: base.mounts.getDoctor.bind(base.mounts),
      runDoctorFixes,
    });
    wrap(<DoctorPage />, service);
    await waitFor(() => expect(screen.getByTestId('run-fixes-btn')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('run-fixes-btn'));
    await waitFor(() => expect(screen.getByTestId('confirm-cancel')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('confirm-cancel'));
    await waitFor(() => expect(screen.queryByTestId('confirm-fix')).not.toBeInTheDocument());
    expect(runDoctorFixes).not.toHaveBeenCalled();
  });

  it('confirming runs the fixes and refreshes the report', async () => {
    wrap(<DoctorPage />);
    await waitFor(() => expect(screen.getByTestId('run-fixes-btn')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('run-fixes-btn'));
    await waitFor(() => expect(screen.getByTestId('confirm-fix')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('confirm-fix'));

    // The two fixable warn checks become pass: score 3/6 → 5/6.
    await waitFor(() => expect(screen.getByTestId('doctor-score').textContent).toBe('5/6'));
    // No fixable, non-passing checks remain — the button disappears.
    expect(screen.queryByTestId('run-fixes-btn')).not.toBeInTheDocument();
    // Worst is still fail (wikilink-integrity is not fixable).
    expect(screen.getByTestId('doctor-worst').textContent).toBe('FAIL');
  });

  it('falls back to invalidation when the fix endpoint returns null', async () => {
    const base = createMimirMockAdapter();
    const getDoctor = vi.fn(base.mounts.getDoctor.bind(base.mounts));
    const service = withMounts({
      getDoctor,
      runDoctorFixes: async () => null,
    });
    wrap(<DoctorPage />, service);
    await waitFor(() => expect(screen.getByTestId('run-fixes-btn')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('run-fixes-btn'));
    await waitFor(() => expect(screen.getByTestId('confirm-fix')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('confirm-fix'));
    await waitFor(() => expect(getDoctor.mock.calls.length).toBeGreaterThan(1));
  });

  it('shows an empty state when the backend has no doctor', async () => {
    const service = withMounts({ getDoctor: async () => null });
    wrap(<DoctorPage />, service);
    await waitFor(() => expect(screen.getByTestId('doctor-empty')).toBeInTheDocument());
    expect(screen.queryByTestId('run-fixes-btn')).not.toBeInTheDocument();
  });

  it('shows error state when the service throws', async () => {
    const service = withMounts({
      getDoctor: async () => {
        throw new Error('doctor service down');
      },
    });
    wrap(<DoctorPage />, service);
    await waitFor(() => expect(screen.getByText('doctor service down')).toBeInTheDocument());
  });

  it('hides the Run fixes button when nothing is fixable', async () => {
    const service = withMounts({
      getDoctor: async () => ({
        score: '1/1',
        worst: 'pass' as const,
        checks: [
          {
            id: 'index-fresh',
            title: 'Mount index freshness',
            status: 'pass' as const,
            detail: 'ok',
            remediation: '',
            fixable: false,
          },
        ],
      }),
    });
    wrap(<DoctorPage />, service);
    await waitFor(() => expect(screen.getByTestId('doctor-score')).toBeInTheDocument());
    expect(screen.queryByTestId('run-fixes-btn')).not.toBeInTheDocument();
    expect(screen.getByTestId('doctor-worst').textContent).toBe('PASS');
  });
});
