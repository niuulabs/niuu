import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { HistoryDetailsContext } from './HistoryDetailsContext';
import { HistoryMessagePreview } from './HistoryMessagePreview';
import { ToolBlock } from './ToolBlock/ToolBlock';
vi.mock('@niuulabs/query', () => ({ getAuthHeaders: () => new Headers() }));
const endpoint = 'ws://forge.test/forge-host/build/s/review/session';
const block = {
  type: 'tool_use' as const,
  id: 'tool',
  name: 'Bash',
  input: { _elided_input: true, preview: 'Large command' },
};
beforeEach(() => vi.stubGlobal('fetch', vi.fn()));
it('fetches elided input/output only when opened, through the owning host', async () => {
  const fetcher = vi.fn(async () =>
    Response.json({
      tool_use_id: 'tool',
      input: { command: 'git status' },
      content: 'Working tree clean',
    }),
  );
  vi.stubGlobal('fetch', fetcher);
  render(
    <HistoryDetailsContext.Provider value={endpoint}>
      <ToolBlock
        block={block}
        result={{ type: 'tool_result', tool_use_id: 'tool', truncated: true }}
      />
    </HistoryDetailsContext.Provider>,
  );
  expect(fetcher).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: /Terminal/ }));
  await screen.findByText('Working tree clean');
  expect(screen.getByText('git status')).toBeVisible();
  expect(fetcher.mock.calls[0]![0]).toContain(
    '/forge-host/build/api/v1/forge/sessions/review/tool-result/tool',
  );
});
it('retains a retryable error if tool expansion fails', async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(new Response(null, { status: 503 }))
    .mockResolvedValueOnce(
      Response.json({ tool_use_id: 'tool', input: { command: 'pwd' }, content: '/workspace' }),
    );
  vi.stubGlobal('fetch', fetcher);
  render(
    <HistoryDetailsContext.Provider value={endpoint}>
      <ToolBlock block={block} />
    </HistoryDetailsContext.Provider>,
  );
  fireEvent.click(screen.getByRole('button'));
  await screen.findByRole('alert');
  fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
  await screen.findByText('/workspace');
});
it('does not mislabel metadata previews as assistant or user text and opens a full-item reader', async () => {
  const fetcher = vi.fn(async () =>
    Response.json({
      turn: {
        id: 'preview',
        role: 'user',
        content: 'Complete message',
        created_at: '2026-09-18T00:00:00Z',
      },
    }),
  );
  vi.stubGlobal('fetch', fetcher);
  render(
    <HistoryDetailsContext.Provider value={endpoint}>
      <HistoryMessagePreview
        message={{
          id: 'preview',
          role: 'assistant',
          content: 'not authoritative',
          createdAt: new Date(),
          historyPreview: true,
          historyMetadataPreview: true,
        }}
      />
    </HistoryDetailsContext.Provider>,
  );
  expect(screen.queryByText('not authoritative')).toBeNull();
  expect(fetcher).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Open full message' }));
  await screen.findByText('Complete message');
  expect(screen.getByRole('dialog')).toHaveClass('niuu-chat-history-reader');
  fireEvent.click(screen.getByRole('button', { name: 'Close' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
});
it('reports an unsupported full-item API and permits retry', async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(new Response(null, { status: 404 }))
    .mockResolvedValueOnce(
      Response.json({
        turn: {
          id: 'preview',
          role: 'assistant',
          content: 'Full answer',
          created_at: '2026-09-18T00:00:00Z',
        },
      }),
    );
  vi.stubGlobal('fetch', fetcher);
  render(
    <HistoryDetailsContext.Provider value={endpoint}>
      <HistoryMessagePreview
        message={{
          id: 'preview',
          role: 'assistant',
          content: 'Excerpt',
          createdAt: new Date(),
          historyPreview: true,
        }}
      />
    </HistoryDetailsContext.Provider>,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Open full message' }));
  await screen.findByRole('alert');
  fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
  await screen.findByText('Full answer');
});
