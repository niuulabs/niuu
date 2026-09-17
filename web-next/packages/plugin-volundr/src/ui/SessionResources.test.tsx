import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';
import { Blob as NodeBlob } from 'node:buffer';
import { MarkdownContent, PresentedFileCard } from '@niuulabs/ui';
import { SessionResources } from './SessionResources';
import { createMockFileSystemPort } from '../adapters/mock';

const blob = (content: string, type = 'text/plain') =>
  new NodeBlob([content], { type }) as unknown as Blob;
const createUrl = vi.fn(() => 'blob:review-file');
const revokeUrl = vi.fn();
beforeEach(() => {
  vi.stubGlobal(
    'URL',
    class extends URL {
      static createObjectURL = createUrl;
      static revokeObjectURL = revokeUrl;
    },
  );
  createUrl.mockClear();
  revokeUrl.mockClear();
});
afterEach(() => vi.unstubAllGlobals());

function setup(
  downloadFile = vi.fn().mockResolvedValue(blob('Document content')),
  markdown = '[Guide](/home/thor/repo/docs/guide.md)',
) {
  const filesystem = {
    ...createMockFileSystemPort(),
    downloadFile,
    downloadPresentedFile: vi.fn().mockResolvedValue(blob('PDF', 'application/pdf')),
  };
  const result = render(
    <SessionResources
      sessionId="owning-session"
      workspace="/home/thor/repo"
      filesystem={filesystem}
    >
      <MarkdownContent content={markdown} />
      <PresentedFileCard
        block={{
          type: 'tool_use',
          id: 'delivery',
          name: 'present_file',
          input: { file_id: 'opaque-id', name: 'report.pdf', mime: 'application/pdf' },
        }}
      />
    </SessionResources>,
  );
  return { ...result, filesystem };
}

describe('session file previews', () => {
  it('loads authenticated workspace content on demand, resolves document-relative links and releases bytes', async () => {
    const download = vi
      .fn()
      .mockResolvedValueOnce(blob('# Guide\n\n[Next](../README.md)', 'text/markdown'))
      .mockResolvedValueOnce(blob('Readme'));
    const { unmount } = setup(download);
    expect(download).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Guide' }));
    await screen.findByRole('heading', { name: 'Guide' });
    expect(download).toHaveBeenCalledWith(
      'owning-session',
      '/workspace/docs/guide.md',
      expect.any(AbortSignal),
    );
    expect(screen.getByRole('link', { name: 'Download guide.md' })).toHaveAttribute(
      'download',
      'guide.md',
    );
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    await screen.findByText('Readme');
    expect(download).toHaveBeenLastCalledWith(
      'owning-session',
      '/workspace/README.md',
      expect.any(AbortSignal),
    );
    unmount();
    expect(revokeUrl).toHaveBeenCalledWith('blob:review-file');
  });
  it('shows loading and failed requests without inventing file contents', async () => {
    let reject!: (reason: Error) => void;
    setup(
      vi.fn(
        () =>
          new Promise((_resolve, rejectPromise) => {
            reject = rejectPromise;
          }),
      ),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Guide' }));
    expect(screen.getByRole('status')).toHaveTextContent('Loading file');
    reject(new Error('File is outside the allowed workspace'));
    expect(await screen.findByRole('alert')).toHaveTextContent('outside the allowed workspace');
    expect(screen.queryByRole('link', { name: /Download/ })).not.toBeInTheDocument();
  });
  it('aborts a pending file load when the preview closes', async () => {
    const download = vi.fn(() => new Promise<Blob>(() => {}));
    const { unmount } = setup(download);
    fireEvent.click(screen.getByRole('button', { name: 'Guide' }));
    const signal = (download.mock.calls[0] as unknown as [string, string, AbortSignal])[2];
    unmount();
    expect(signal.aborted).toBe(true);
  });
  it('routes delivered PDFs by opaque file id', async () => {
    const { filesystem } = setup();
    fireEvent.click(screen.getByRole('button', { name: 'Open file' }));
    await waitFor(() =>
      expect(screen.getByTitle('report.pdf')).toHaveAttribute('src', 'blob:review-file'),
    );
    expect(filesystem.downloadPresentedFile).toHaveBeenCalledWith(
      'owning-session',
      'opaque-id',
      expect.any(AbortSignal),
    );
    expect(filesystem.downloadFile).not.toHaveBeenCalled();
  });
  it('loads local Markdown images and opens their preview', async () => {
    const download = vi.fn().mockResolvedValue(blob('image bytes', 'image/png'));
    setup(download, '![Diagram](./diagram.png)');
    expect(await screen.findByRole('img', { name: 'Diagram' })).toHaveAttribute(
      'src',
      'blob:review-file',
    );
    fireEvent.click(screen.getByRole('button', { name: 'Open image Diagram' }));
    await screen.findByRole('button', { name: 'Download', exact: true });
    expect(screen.getByRole('img', { name: 'diagram.png' })).toBeInTheDocument();
  });
  it('sandboxes HTML without scripts and offers literal source, with a bounded text preview', async () => {
    const download = vi.fn().mockResolvedValue(blob('<script>bad()</script>', 'text/html'));
    const first = setup(download, '[HTML](./page.html)');
    fireEvent.click(screen.getByRole('button', { name: 'HTML' }));
    expect(await screen.findByTitle('page.html')).toHaveAttribute('sandbox', '');
    expect(screen.getByTitle('page.html')).toHaveAttribute('referrerpolicy', 'no-referrer');
    fireEvent.click(screen.getByRole('button', { name: 'Source', exact: true }));
    expect(screen.getByText('<script>bad()</script>')).toBeInTheDocument();
    expect(document.querySelector('script')).toBeNull();
    first.unmount();
    setup(vi.fn().mockResolvedValue(blob('x'.repeat(2 * 1024 * 1024 + 1))), '[Large](./large.txt)');
    fireEvent.click(screen.getByRole('button', { name: 'Large' }));
    expect(await screen.findByText(/larger than the 2 MiB/)).toBeInTheDocument();
  });
});
