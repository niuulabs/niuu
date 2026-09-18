import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { Blob as NodeBlob } from 'node:buffer';
import { ExternalLinkPreview } from './ExternalLinkPreview';
import { ConversationResourceProvider } from './ConversationResources';

const fetchFile = vi.fn();
const revoke = vi.fn();
beforeEach(() => {
  vi.stubGlobal('fetch', fetchFile);
  vi.stubGlobal(
    'URL',
    class extends URL {
      static createObjectURL = () => 'blob:document';
      static revokeObjectURL = revoke;
    },
  );
  fetchFile.mockReset();
  revoke.mockClear();
});
afterEach(() => vi.unstubAllGlobals());
const response = (text: string) => ({
  ok: true,
  blob: async () => new NodeBlob([text], { type: 'text/markdown' }),
});

it('loads real Markdown, copies and downloads it, and resolves nested links against the remote document', async () => {
  const copy = vi.fn().mockResolvedValue(undefined);
  vi.stubGlobal('navigator', { clipboard: { writeText: copy } });
  fetchFile.mockResolvedValue(response('# Review\n\n[Next](../next.md)\n\n[Section](#review)'));
  const { unmount } = render(
    <ExternalLinkPreview href="https://example.test/docs/review.md" name="review.md" />,
  );
  expect(screen.getByText('Loading document…')).toBeInTheDocument();
  await screen.findByRole('heading', { name: 'Review' });
  expect(fetchFile).toHaveBeenCalledWith(
    'https://example.test/docs/review.md',
    expect.objectContaining({ credentials: 'omit', signal: expect.any(AbortSignal) }),
  );
  expect(screen.getByRole('link', { name: 'Download' })).toHaveAttribute('download', 'review.md');
  expect(screen.getByRole('link', { name: 'Section' })).toHaveAttribute('href', '#review');
  fireEvent.click(screen.getByRole('button', { name: 'Copy text' }));
  await screen.findByText('Copied');
  expect(copy).toHaveBeenCalledWith(expect.stringContaining('# Review'));
  fireEvent.click(screen.getByRole('button', { name: 'Next' }));
  await waitFor(() =>
    expect(fetchFile).toHaveBeenLastCalledWith('https://example.test/next.md', expect.any(Object)),
  );
  unmount();
  expect(revoke).toHaveBeenCalledWith('blob:document');
});
it('uses the owning session to replace the current preview', async () => {
  const open = vi.fn();
  fetchFile.mockResolvedValue(response('[Next](./next.md)'));
  render(
    <ConversationResourceProvider port={{ resolve: () => null, load: vi.fn(), open }}>
      <ExternalLinkPreview href="https://example.test/docs/review.md" name="review.md" />
    </ConversationResourceProvider>,
  );
  fireEvent.click(await screen.findByRole('button', { name: 'Next' }));
  expect(open).toHaveBeenCalledWith({
    kind: 'external',
    path: 'https://example.test/docs/next.md',
    name: 'next.md',
  });
});
it.each([
  [() => Promise.reject(new Error('CORS denied')), 'CORS denied'],
  [() => Promise.reject('unavailable'), 'Could not load document'],
  [() => Promise.resolve({ ok: false, status: 404 }), '404'],
  [() => Promise.resolve(response('x'.repeat(2 * 1024 * 1024 + 1))), 'larger than the 2 MiB'],
])('shows a failed or oversized preview inside the panel', async (request, message) => {
  fetchFile.mockImplementation(request);
  render(<ExternalLinkPreview href="https://example.test/review.md" name="review.md" />);
  expect(await screen.findByRole('alert')).toHaveTextContent(message);
  expect(screen.getByRole('link', { name: 'Open in new tab' })).toHaveAttribute(
    'href',
    'https://example.test/review.md',
  );
  expect(screen.queryByRole('link', { name: 'Download' })).not.toBeInTheDocument();
});
it('aborts unfinished document loads when the panel closes', () => {
  fetchFile.mockImplementation(() => new Promise(() => {}));
  const { unmount } = render(
    <ExternalLinkPreview href="https://example.test/review.md" name="review.md" />,
  );
  const signal = fetchFile.mock.calls[0]![1].signal;
  unmount();
  expect(signal.aborted).toBe(true);
});
it('contains website navigation and explains embedding failures without claiming success', async () => {
  const copy = vi.fn().mockRejectedValue(new Error('denied'));
  vi.stubGlobal('navigator', { clipboard: { writeText: copy } });
  render(<ExternalLinkPreview href="https://example.test" name="Example" />);
  const iframe = screen.getByTitle('Preview of Example');
  expect(iframe).toHaveAttribute('sandbox', 'allow-scripts allow-forms');
  expect(iframe).toHaveAttribute('referrerpolicy', 'no-referrer');
  expect(screen.getByText(/If this site restricts embedded previews/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Copy link' }));
  expect(await screen.findByText(/Could not copy/)).toBeInTheDocument();
  expect(fetchFile).not.toHaveBeenCalled();
});
it('previews email links without opening a mail app and rejects executable URLs', () => {
  const { unmount } = render(
    <ExternalLinkPreview href="mailto:review@example.test" name="Email" />,
  );
  expect(screen.getByText('review@example.test')).toBeInTheDocument();
  expect(screen.getByRole('link', { name: 'Open mail app' })).toHaveAttribute(
    'href',
    'mailto:review@example.test',
  );
  expect(document.querySelector('iframe')).toBeNull();
  unmount();
  render(<ExternalLinkPreview href="javascript:alert(1)" name="Unsafe" />);
  expect(screen.getByRole('alert')).toHaveTextContent('cannot be previewed');
});
