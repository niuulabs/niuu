import { describe, it, expect } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { LogView } from './LogView';
import { createMockSessionStream, createMockRavenStream } from '../adapters/mock';
import { wrapWithServices } from '../testing/wrapWithRavn';

const wrap = wrapWithServices;

const services = {
  'ravn.sessions': createMockSessionStream(),
  'ravn.ravens': createMockRavenStream(),
};

describe('LogView', () => {
  it('renders the log table', async () => {
    render(<LogView />, { wrapper: wrap(services) });
    await waitFor(() => expect(screen.getByLabelText(/log stream/i)).toBeInTheDocument(), {
      timeout: 3000,
    });
  });

  it('shows all four column headers', async () => {
    render(<LogView />, { wrapper: wrap(services) });
    await waitFor(() => {
      expect(screen.getByText('time')).toBeInTheDocument();
      expect(screen.getByText('raven')).toBeInTheDocument();
      expect(screen.getByText('kind')).toBeInTheDocument();
      expect(screen.getByText('body')).toBeInTheDocument();
    });
  });

  it('shows log entries from sessions', async () => {
    render(<LogView />, { wrapper: wrap(services) });
    // Wait for entries to appear (mock sessions have messages)
    await waitFor(() =>
      expect(screen.getByRole('log', { name: /event log/i })).toBeInTheDocument(),
    );
    await waitFor(
      () => {
        const rows = document.querySelectorAll('.rv-log-row');
        expect(rows.length).toBeGreaterThan(0);
      },
      { timeout: 3000 },
    );
  });

  it('renders the search input', () => {
    render(<LogView />, { wrapper: wrap(services) });
    expect(screen.getByRole('searchbox', { name: /search log/i })).toBeInTheDocument();
  });

  it('renders kind filter buttons', () => {
    render(<LogView />, { wrapper: wrap(services) });
    expect(screen.getByRole('button', { name: 'user' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'asst' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'emit' })).toBeInTheDocument();
  });

  it('kind filter toggles aria-pressed', () => {
    render(<LogView />, { wrapper: wrap(services) });
    const userBtn = screen.getByRole('button', { name: 'user' });
    expect(userBtn).toHaveAttribute('aria-pressed', 'false');
    fireEvent.click(userBtn);
    expect(userBtn).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(userBtn);
    expect(userBtn).toHaveAttribute('aria-pressed', 'false');
  });

  it('auto-tail checkbox is checked by default', () => {
    render(<LogView />, { wrapper: wrap(services) });
    const checkbox = screen.getByRole('checkbox', { name: /auto-tail/i });
    expect(checkbox).toBeChecked();
  });

  it('unchecking auto-tail works', () => {
    render(<LogView />, { wrapper: wrap(services) });
    const checkbox = screen.getByRole('checkbox', { name: /auto-tail/i });
    fireEvent.click(checkbox);
    expect(checkbox).not.toBeChecked();
  });

  it('shows footer with entry count', async () => {
    render(<LogView />, { wrapper: wrap(services) });
    await waitFor(() => expect(screen.getByText(/entries/i)).toBeInTheDocument(), {
      timeout: 3000,
    });
  });

  it('raven selector renders options', async () => {
    render(<LogView />, { wrapper: wrap(services) });
    await waitFor(() => {
      const select = screen.getByRole('combobox', { name: /filter by raven/i });
      expect(select).toBeInTheDocument();
    });
  });
});
