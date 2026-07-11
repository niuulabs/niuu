import { describe, it, expect } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { TriggersView } from './TriggersView';
import { createMockTriggerStore } from '../adapters/mock';
import { wrapWithServices } from '../testing/wrapWithRavn';

const wrap = wrapWithServices;

const services = { 'ravn.triggers': createMockTriggerStore() };

describe('TriggersView', () => {
  it('shows loading state initially', () => {
    render(<TriggersView />, { wrapper: wrap(services) });
    expect(screen.getByText(/loading triggers/i)).toBeInTheDocument();
  });

  it('renders trigger groups after loading', async () => {
    render(<TriggersView />, { wrapper: wrap(services) });
    await waitFor(() => expect(screen.getByText(/cron/i)).toBeInTheDocument());
  });

  it('shows all four kind groups', async () => {
    render(<TriggersView />, { wrapper: wrap(services) });
    await waitFor(() => {
      expect(screen.getByRole('region', { name: /cron triggers/i })).toBeInTheDocument();
      expect(screen.getByRole('region', { name: /event triggers/i })).toBeInTheDocument();
      expect(screen.getByRole('region', { name: /webhook triggers/i })).toBeInTheDocument();
      expect(screen.getByRole('region', { name: /manual triggers/i })).toBeInTheDocument();
    });
  });

  it('shows total and active counts', async () => {
    render(<TriggersView />, { wrapper: wrap(services) });
    await waitFor(() => expect(screen.getByText(/total/i)).toBeInTheDocument());
  });

  it('shows persona names in rows', async () => {
    render(<TriggersView />, { wrapper: wrap(services) });
    await waitFor(() => {
      expect(screen.getByText('eir')).toBeInTheDocument();
      expect(screen.getByText('fjölnir')).toBeInTheDocument();
    });
  });

  it('shows cron spec in code element', async () => {
    render(<TriggersView />, { wrapper: wrap(services) });
    await waitFor(() => expect(screen.getByText('0 * * * *')).toBeInTheDocument());
  });

  it('shows error state when service fails', async () => {
    const failing = {
      listTriggers: async () => {
        throw new Error('fetch failed');
      },
    };
    render(<TriggersView />, { wrapper: wrap({ 'ravn.triggers': failing }) });
    await waitFor(() => expect(screen.getByText(/failed to load triggers/i)).toBeInTheDocument());
  });

  it('renders empty state when the service returns no triggers', async () => {
    const empty = { listTriggers: async () => [] };
    render(<TriggersView />, { wrapper: wrap({ 'ravn.triggers': empty }) });
    await waitFor(() => expect(screen.getByText(/no triggers configured/i)).toBeInTheDocument());
    expect(document.querySelector('.rv-triggers-view__count')).toHaveTextContent(
      '0 active · 0 total',
    );
  });

  it('renders disabled triggers while omitting empty kind groups', async () => {
    const disabled = {
      listTriggers: async () => [
        {
          id: 'trigger-disabled',
          kind: 'event' as const,
          personaName: 'reviewer',
          spec: 'review.requested',
          enabled: false,
          createdAt: '2026-07-11T12:00:00Z',
        },
      ],
    };
    render(<TriggersView />, { wrapper: wrap({ 'ravn.triggers': disabled }) });
    await waitFor(() => expect(screen.getByText('disabled')).toBeInTheDocument());
    expect(document.querySelector('.rv-triggers-view__count')).toHaveTextContent(
      '0 active · 1 total',
    );
    expect(screen.getByRole('region', { name: /event triggers/i })).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: /cron triggers/i })).not.toBeInTheDocument();
  });
});
