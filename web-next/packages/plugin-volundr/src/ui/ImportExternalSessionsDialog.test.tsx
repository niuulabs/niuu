import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import {
  ImportExternalSessionsDialog,
  externalSessionKey,
  externalSessionTitle,
  externalSessionActivityTs,
} from './ImportExternalSessionsDialog';
import { createMockVolundrService } from '../adapters/mock';
import type { IVolundrService } from '../ports/IVolundrService';
import type { ExternalSession, VolundrSession } from '../models/volundr.model';

function makeExternalSession(overrides: Partial<ExternalSession> = {}): ExternalSession {
  return {
    provider: 'claude-code',
    harness: 'claude',
    externalId: 'ext-1',
    workspacePath: '/Users/dev/code/volundr',
    title: 'fix import flow',
    model: 'claude-sonnet',
    createdAt: '2026-06-01T08:00:00Z',
    updatedAt: '2026-06-01T09:00:00Z',
    live: false,
    workspaceExists: true,
    workspaceAllowed: true,
    importedSessionId: null,
    ...overrides,
  };
}

function makeImportedSession(overrides: Partial<VolundrSession> = {}): VolundrSession {
  return {
    id: 'sess-imported',
    name: 'fix import flow',
    source: { type: 'git', repo: '/Users/dev/code/volundr', branch: 'main' },
    status: 'stopped',
    model: 'claude-sonnet',
    lastActive: Date.now(),
    messageCount: 0,
    tokensUsed: 0,
    origin: 'claude',
    externalSessionId: 'ext-1',
    ...overrides,
  };
}

function wrap(
  volundr: IVolundrService,
  props: Partial<Parameters<typeof ImportExternalSessionsDialog>[0]> = {},
) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ServicesProvider services={{ volundr }}>
        <ImportExternalSessionsDialog open onOpenChange={() => {}} {...props} />
      </ServicesProvider>
    </QueryClientProvider>,
  );
}

describe('ImportExternalSessionsDialog helpers', () => {
  it('derives row keys, titles, and activity timestamps with fallbacks', () => {
    const session = makeExternalSession();
    expect(externalSessionKey(session)).toBe('claude-code:ext-1');
    expect(externalSessionTitle(session)).toBe('fix import flow');
    expect(externalSessionTitle(makeExternalSession({ title: '   ' }))).toBe('ext-1');
    expect(externalSessionActivityTs(session)).toBe(Date.parse('2026-06-01T09:00:00Z'));
    expect(externalSessionActivityTs(makeExternalSession({ updatedAt: null }))).toBe(
      Date.parse('2026-06-01T08:00:00Z'),
    );
    expect(
      externalSessionActivityTs(makeExternalSession({ updatedAt: null, createdAt: null })),
    ).toBeNull();
    expect(
      externalSessionActivityTs(makeExternalSession({ updatedAt: 'not-a-date', createdAt: null })),
    ).toBeNull();
  });
});

describe('ImportExternalSessionsDialog', () => {
  it('lists external sessions with harness label, title, workspace path, and activity', async () => {
    const volundr = createMockVolundrService();
    volundr.listExternalSessions = vi
      .fn()
      .mockResolvedValue([makeExternalSession(), makeExternalSession({ externalId: 'ext-2' })]);

    wrap(volundr);

    await waitFor(() => expect(screen.getByTestId('external-session-list')).toBeInTheDocument());
    const row = screen.getByTestId('external-session-row-ext-1');
    expect(row).toHaveTextContent('Claude Code');
    expect(row).toHaveTextContent('fix import flow');
    expect(row).toHaveTextContent('/Users/dev/code/volundr');
    expect(row).toHaveTextContent(/ago/i);
    expect(screen.getByTestId('external-session-row-ext-2')).toBeInTheDocument();
  });

  it('falls back to the external id when the title is empty', async () => {
    const volundr = createMockVolundrService();
    volundr.listExternalSessions = vi
      .fn()
      .mockResolvedValue([makeExternalSession({ title: '', harness: 'codex' })]);

    wrap(volundr);

    await waitFor(() =>
      expect(screen.getByTestId('external-session-row-ext-1')).toHaveTextContent('ext-1'),
    );
    expect(screen.getByTestId('external-session-row-ext-1')).toHaveTextContent('Codex');
  });

  it('shows a live badge only for live sessions', async () => {
    const volundr = createMockVolundrService();
    volundr.listExternalSessions = vi
      .fn()
      .mockResolvedValue([
        makeExternalSession({ live: true }),
        makeExternalSession({ externalId: 'ext-2', live: false }),
      ]);

    wrap(volundr);

    await waitFor(() =>
      expect(screen.getByTestId('external-session-live-ext-1')).toBeInTheDocument(),
    );
    expect(screen.queryByTestId('external-session-live-ext-2')).not.toBeInTheDocument();
  });

  it('disables the import button with an explanation when the workspace is missing', async () => {
    const volundr = createMockVolundrService();
    volundr.listExternalSessions = vi
      .fn()
      .mockResolvedValue([makeExternalSession({ workspaceExists: false })]);

    wrap(volundr);

    await waitFor(() =>
      expect(screen.getByTestId('external-session-import-ext-1')).toBeInTheDocument(),
    );
    const button = screen.getByTestId('external-session-import-ext-1');
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute('title', 'Cannot import: workspace directory no longer exists');
    expect(screen.getByTestId('external-session-missing-workspace-ext-1')).toBeInTheDocument();
  });

  it('disables the import button when the workspace violates the mount policy', async () => {
    const volundr = createMockVolundrService();
    volundr.listExternalSessions = vi
      .fn()
      .mockResolvedValue([makeExternalSession({ workspaceAllowed: false })]);

    wrap(volundr);

    await waitFor(() =>
      expect(screen.getByTestId('external-session-import-ext-1')).toBeInTheDocument(),
    );
    const button = screen.getByTestId('external-session-import-ext-1');
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute(
      'title',
      'Cannot import: workspace is outside the allowed mount prefixes',
    );
    expect(screen.getByTestId('external-session-workspace-not-allowed-ext-1')).toBeInTheDocument();
  });

  it('shows an imported state instead of the button when already imported', async () => {
    const volundr = createMockVolundrService();
    volundr.listExternalSessions = vi
      .fn()
      .mockResolvedValue([makeExternalSession({ importedSessionId: 'sess-9' })]);

    wrap(volundr);

    await waitFor(() =>
      expect(screen.getByTestId('external-session-imported-ext-1')).toBeInTheDocument(),
    );
    expect(screen.queryByTestId('external-session-import-ext-1')).not.toBeInTheDocument();
  });

  it('imports a session, refreshes the list, and notifies the parent', async () => {
    const volundr = createMockVolundrService();
    const listExternalSessions = vi
      .fn()
      .mockResolvedValueOnce([makeExternalSession()])
      .mockResolvedValue([makeExternalSession({ importedSessionId: 'sess-imported' })]);
    const importExternalSession = vi.fn().mockResolvedValue(makeImportedSession());
    volundr.listExternalSessions = listExternalSessions;
    volundr.importExternalSession = importExternalSession;
    const onImported = vi.fn();

    wrap(volundr, { onImported });

    await waitFor(() =>
      expect(screen.getByTestId('external-session-import-ext-1')).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByTestId('external-session-import-ext-1'));

    await waitFor(() => expect(importExternalSession).toHaveBeenCalledWith('claude-code', 'ext-1'));
    await waitFor(() =>
      expect(screen.getByTestId('external-session-imported-ext-1')).toBeInTheDocument(),
    );
    expect(listExternalSessions.mock.calls.length).toBeGreaterThanOrEqual(2);
    expect(onImported).toHaveBeenCalledWith(expect.objectContaining({ id: 'sess-imported' }));
  });

  it('surfaces an inline error when the import fails', async () => {
    const volundr = createMockVolundrService();
    volundr.listExternalSessions = vi.fn().mockResolvedValue([makeExternalSession()]);
    volundr.importExternalSession = vi
      .fn()
      .mockRejectedValue(new Error('Workspace directory missing'));

    wrap(volundr);

    await waitFor(() =>
      expect(screen.getByTestId('external-session-import-ext-1')).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByTestId('external-session-import-ext-1'));

    await waitFor(() =>
      expect(screen.getByTestId('external-session-import-error-ext-1')).toHaveTextContent(
        'Workspace directory missing',
      ),
    );
  });

  it('shows a graceful unavailable state on 503 instead of an error', async () => {
    const volundr = createMockVolundrService();
    volundr.listExternalSessions = vi
      .fn()
      .mockRejectedValue(Object.assign(new Error('not available'), { status: 503 }));

    wrap(volundr);

    await waitFor(() => expect(screen.getByText(/discovery unavailable/i)).toBeInTheDocument());
    expect(screen.queryByText(/failed to discover/i)).not.toBeInTheDocument();
  });

  it('shows an error state for non-503 failures', async () => {
    const volundr = createMockVolundrService();
    volundr.listExternalSessions = vi.fn().mockRejectedValue(new Error('boom'));

    wrap(volundr);

    await waitFor(() =>
      expect(screen.getByText(/failed to discover external sessions/i)).toBeInTheDocument(),
    );
    expect(screen.getByText('boom')).toBeInTheDocument();
  });

  it('shows an empty state when no sessions are discovered', async () => {
    const volundr = createMockVolundrService();
    volundr.listExternalSessions = vi.fn().mockResolvedValue([]);

    wrap(volundr);

    await waitFor(() =>
      expect(screen.getByText(/no external sessions found/i)).toBeInTheDocument(),
    );
  });

  it('shows a loading state while discovering', () => {
    const volundr = createMockVolundrService();
    volundr.listExternalSessions = vi.fn().mockReturnValue(new Promise(() => {}));

    wrap(volundr);

    expect(screen.getByText(/discovering external sessions/i)).toBeInTheDocument();
  });
});
