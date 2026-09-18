import { afterEach, describe, expect, it, vi } from 'vitest';
import { waitFor } from '@testing-library/react';
import { ToolImageLoader, loadToolImage, toolResultUrl } from './toolImageLoader';
import type { ToolImage } from './toolImages';

vi.mock('@niuulabs/query', () => ({
  getAuthHeaders: () => new Headers({ authorization: 'Bearer test-only' }),
}));
const endpoint = 'wss://forge.test/forge-host/build/s/session/session';
const image = (id: string): ToolImage => ({ toolUseId: id, index: 0, name: `${id}.png` });
const jpeg = () =>
  new Response(new Uint8Array([255, 216, 255]), { headers: { 'content-type': 'image/jpeg' } });
afterEach(() => vi.unstubAllGlobals());
describe('session image loading', () => {
  it('preserves remote host routing and escapes tool ids', () => {
    expect(toolResultUrl(endpoint, 'tool/id', true, 1).href).toBe(
      'https://forge.test/forge-host/build/api/v1/forge/sessions/session/tool-result/tool%2Fid/preview?image_index=1',
    );
    expect(toolResultUrl('ws://standalone.test/api/session', 'tool').pathname).toBe(
      '/api/conversation/tool-result/tool',
    );
  });
  it('coalesces thumbnails, limits concurrent reads and never loads full results automatically', async () => {
    const pending: Array<() => void> = [];
    const fetcher = vi.fn(
      () => new Promise<Response>((resolve) => pending.push(() => resolve(jpeg()))),
    );
    vi.stubGlobal('fetch', fetcher);
    const loader = new ToolImageLoader(endpoint);
    const unsubscribers = ['a', 'b', 'c', 'd'].map((id) => loader.subscribe(image(id), vi.fn()));
    unsubscribers.push(loader.subscribe(image('a'), vi.fn()));
    expect(fetcher).toHaveBeenCalledTimes(3);
    pending.shift()!();
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(4));
    expect(loader.snapshot(image('a')).status).toBe('ready');
    expect(fetcher.mock.calls.every(([url]) => String(url).endsWith('/preview'))).toBe(true);
    expect(fetcher.mock.calls[0]![1].headers.get('authorization')).toBe('Bearer test-only');
    unsubscribers.forEach((unsubscribe) => unsubscribe());
    loader.dispose();
    pending.forEach((finish) => finish());
  });
  it('reports failed/invalid previews, retries and does not fetch full images as a fallback', async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(new Response('', { status: 404 }))
      .mockResolvedValueOnce(new Response('<html>', { headers: { 'content-type': 'text/html' } }))
      .mockResolvedValueOnce(jpeg());
    vi.stubGlobal('fetch', fetcher);
    const loader = new ToolImageLoader(endpoint);
    loader.subscribe(image('a'), vi.fn());
    await waitFor(() => expect(loader.snapshot(image('a')).status).toBe('error'));
    expect(fetcher).toHaveBeenCalledTimes(1);
    loader.retry(image('a'));
    await waitFor(() => expect(loader.snapshot(image('a')).error).toContain('invalid image'));
    loader.retry(image('a'));
    await waitFor(() => expect(loader.snapshot(image('a')).status).toBe('ready'));
    loader.dispose();
  });
  it('cancels obsolete session requests and rejects oversized thumbnails', async () => {
    const fetcher = vi.fn().mockResolvedValue(
      new Response('', {
        headers: { 'content-type': 'image/jpeg', 'content-length': '99999999' },
      }),
    );
    vi.stubGlobal('fetch', fetcher);
    const loader = new ToolImageLoader(endpoint);
    loader.subscribe(image('a'), vi.fn());
    await waitFor(() => expect(loader.snapshot(image('a')).error).toContain('size limit'));
    loader.dispose();
    expect(fetcher.mock.calls[0]![1].signal.aborted).toBe(true);
  });
  it('fetches the exact image on explicit open and validates the tool identity', async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(Response.json({ tool_use_id: 'wrong', content: [] }))
      .mockResolvedValueOnce(
        Response.json({
          tool_use_id: 'a',
          content: [{ type: 'input_image', image_url: 'data:image/png;base64,AAAA' }],
        }),
      )
      .mockResolvedValueOnce(new Response(new Uint8Array([1, 2, 3])));
    vi.stubGlobal('fetch', fetcher);
    await expect(loadToolImage(image('a'), endpoint, new AbortController().signal)).rejects.toThrow(
      'did not match',
    );
    const result = await loadToolImage(image('a'), endpoint, new AbortController().signal);
    expect(result.type).toBe('image/png');
    expect(result.size).toBe(3);
    expect(fetcher.mock.calls[2]![0]).toBe('data:image/png;base64,AAAA');
  });
  it('uses already-delivered bytes and refuses unsupported active formats', async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(new Uint8Array([1])));
    vi.stubGlobal('fetch', fetcher);
    const content = { type: 'image', file: { type: 'image/png', base64: 'AAAA' } };
    await loadToolImage({ ...image('a'), content }, null, new AbortController().signal);
    expect(fetcher).toHaveBeenCalledTimes(1);
    await expect(
      loadToolImage(
        {
          ...image('a'),
          content: { ...content, file: { ...content.file, type: 'image/svg+xml' } },
        },
        null,
        new AbortController().signal,
      ),
    ).rejects.toThrow('supported image');
  });
});
