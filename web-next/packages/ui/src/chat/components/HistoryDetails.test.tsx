import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { HistoryDetailsContext } from './HistoryDetailsContext';
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
