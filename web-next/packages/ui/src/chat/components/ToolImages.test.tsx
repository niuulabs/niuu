import { beforeEach, afterEach, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ToolImageCard, ToolImageProvider, toolImageSize } from './ToolImages';
import { ToolImagePreview } from './ToolImagePreview';
import {
  ConversationResourceProvider,
  type ConversationResourcePort,
} from './ConversationResources';
import type { ToolImage } from '../toolImages';

vi.mock('@niuulabs/query', () => ({ getAuthHeaders: () => new Headers() }));
const image: ToolImage = {
  toolUseId: 'read',
  index: 0,
  name: 'chart.png',
  path: '/workspace/chart.png',
  mime: 'image/png',
  width: 600,
  height: 1200,
};
const endpoint = 'wss://forge.test/forge-host/build/s/review/session';
const createUrl = vi.fn(() => 'blob:thumbnail');
const revokeUrl = vi.fn();
beforeEach(() => {
  createUrl.mockClear();
  revokeUrl.mockClear();
  vi.stubGlobal(
    'URL',
    class extends URL {
      static createObjectURL = createUrl;
      static revokeObjectURL = revokeUrl;
    },
  );
  vi.stubGlobal(
    'fetch',
    vi
      .fn()
      .mockResolvedValue(
        new Response(new Uint8Array([1, 2, 3]), { headers: { 'content-type': 'image/jpeg' } }),
      ),
  );
});
afterEach(() => vi.unstubAllGlobals());

it('uses a 200px-high aspect-correct thumbnail and opens the session’s existing preview flow', async () => {
  const port: ConversationResourcePort = { resolve: () => null, load: vi.fn(), open: vi.fn() };
  const { container, unmount } = render(
    <ConversationResourceProvider port={port}>
      <ToolImageProvider endpoint={endpoint}>
        <ToolImageCard image={image} />
      </ToolImageProvider>
    </ConversationResourceProvider>,
  );
  await waitFor(() =>
    expect(container.querySelector('image')).toHaveAttribute('href', 'blob:thumbnail'),
  );
  expect(container.querySelector('svg')).toHaveAttribute('width', '100');
  expect(container.querySelector('svg')).toHaveAttribute('height', '200');
  fireEvent.click(screen.getByRole('button', { name: 'Open image chart.png' }));
  expect(port.open).toHaveBeenCalledWith(
    expect.objectContaining({
      kind: 'tool-image',
      name: 'chart.png',
      preview: expect.any(Blob),
      loadFull: expect.any(Function),
    }),
  );
  expect(fetch).toHaveBeenCalledTimes(1);
  unmount();
  expect(revokeUrl).toHaveBeenCalledWith('blob:thumbnail');
});

it('retains the same reserved box while loading, failing, and retrying a thumbnail', async () => {
  vi.stubGlobal(
    'fetch',
    vi
      .fn()
      .mockResolvedValueOnce(new Response(null, { status: 503 }))
      .mockResolvedValueOnce(
        new Response(new Uint8Array([1]), { headers: { 'content-type': 'image/png' } }),
      ),
  );
  const { container } = render(
    <ToolImageProvider endpoint={endpoint}>
      <ToolImageCard image={image} />
    </ToolImageProvider>,
  );
  const size = container.querySelector('svg')?.getAttribute('viewBox');
  fireEvent.click(await screen.findByRole('button', { name: 'Retry thumbnail' }));
  await waitFor(() => expect(container.querySelector('image')).not.toBeNull());
  expect(container.querySelector('svg')).toHaveAttribute('viewBox', size);
});

it('shows the cached thumbnail immediately but enables transfers only for the full original', async () => {
  let finish!: (blob: Blob) => void;
  const full = new Blob(['full'], { type: 'image/png' });
  const loadFull = vi.fn(
    () =>
      new Promise<Blob>((resolve) => {
        finish = resolve;
      }),
  );
  createUrl.mockReturnValueOnce('blob:preview').mockReturnValueOnce('blob:full');
  const { container, unmount } = render(
    <ToolImagePreview
      resource={{
        kind: 'tool-image',
        path: 'read:0',
        name: 'chart.png',
        preview: new Blob(['small']),
        loadFull,
      }}
    />,
  );
  await waitFor(() =>
    expect(container.querySelector('image')).toHaveAttribute('href', 'blob:preview'),
  );
  expect(screen.getByRole('button', { name: 'Download' })).toBeDisabled();
  expect(screen.queryByRole('link', { name: 'Open original image' })).toBeNull();
  finish(full);
  await waitFor(() =>
    expect(container.querySelector('image')).toHaveAttribute('href', 'blob:full'),
  );
  expect(screen.getByRole('button', { name: 'Download' })).toBeEnabled();
  expect(screen.getByRole('button', { name: 'Copy image' })).toBeEnabled();
  expect(screen.getByRole('link', { name: 'Open original image' })).toHaveAttribute(
    'href',
    'blob:full',
  );
  unmount();
  expect(revokeUrl).toHaveBeenCalledWith('blob:preview');
  expect(revokeUrl).toHaveBeenCalledWith('blob:full');
});

it('retains the thumbnail on a full-image failure, retries, and cancels on close', async () => {
  const loadFull = vi
    .fn()
    .mockRejectedValueOnce(new Error('Image no longer available'))
    .mockResolvedValueOnce(new Blob(['image'], { type: 'image/png' }));
  const { unmount } = render(
    <ToolImagePreview
      resource={{
        kind: 'tool-image',
        path: 'read:0',
        name: 'chart.png',
        preview: new Blob(['small']),
        loadFull,
      }}
    />,
  );
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Showing thumbnail. Image no longer available',
  );
  fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
  await waitFor(() => expect(screen.getByRole('button', { name: 'Download' })).toBeEnabled());
  unmount();
  expect(loadFull.mock.calls[1]![0].aborted).toBe(true);
});

it('opens a shared image dialog when embedded without a session resource host', async () => {
  vi.stubGlobal(
    'fetch',
    vi
      .fn()
      .mockResolvedValueOnce(
        new Response(new Uint8Array([1]), { headers: { 'content-type': 'image/png' } }),
      )
      .mockResolvedValueOnce(
        Response.json({
          tool_use_id: 'read',
          content: { type: 'image', file: { type: 'image/png', base64: 'AAAA' } },
        }),
      )
      .mockResolvedValueOnce(new Response(new Uint8Array([1]))),
  );
  render(
    <ToolImageProvider endpoint={endpoint}>
      <ToolImageCard image={image} />
    </ToolImageProvider>,
  );
  await waitFor(() => expect(createUrl).toHaveBeenCalled());
  fireEvent.click(screen.getByRole('button', { name: 'Open image chart.png' }));
  expect(screen.getByRole('dialog')).toHaveClass('niuu-chat-image-dialog');
  await waitFor(() => expect(screen.getByRole('button', { name: 'Download' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: 'Close' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
});

it('preserves wide, portrait and tiny image aspect ratios', () => {
  expect(toolImageSize({ ...image, width: 1600, height: 400 })).toEqual({
    width: 400,
    height: 100,
  });
  expect(toolImageSize({ ...image, width: 10, height: 20 })).toEqual({ width: 10, height: 20 });
  expect(toolImageSize({ ...image, width: undefined, height: undefined })).toEqual({
    width: 267,
    height: 200,
  });
});

it('offers retry when an image response cannot be decoded', async () => {
  createUrl.mockReturnValueOnce('blob:corrupt').mockReturnValueOnce('blob:valid');
  vi.stubGlobal(
    'fetch',
    vi
      .fn()
      .mockImplementation(
        async () => new Response(new Uint8Array([1]), { headers: { 'content-type': 'image/png' } }),
      ),
  );
  const { container } = render(
    <ToolImageProvider endpoint={endpoint}>
      <ToolImageCard image={image} />
    </ToolImageProvider>,
  );
  await waitFor(() => expect(container.querySelector('image')).not.toBeNull());
  fireEvent.error(container.querySelector('image')!);
  fireEvent.click(await screen.findByRole('button', { name: 'Retry thumbnail' }));
  await waitFor(() =>
    expect(container.querySelector('image')).toHaveAttribute('href', 'blob:valid'),
  );
});
