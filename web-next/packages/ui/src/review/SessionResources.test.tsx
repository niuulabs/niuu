import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';
import { Blob as NodeBlob } from 'node:buffer';
import { MarkdownContent, PresentedFileCard } from '../chat';
import { SessionResources } from './SessionResources';
import { useConversationResources } from '../chat/components/ConversationResources';
import type { IFileSystemPort } from './IFileSystemPort';

function fileSystemStub(): IFileSystemPort {
  return {
    listTree: vi.fn(),
    expandDirectory: vi.fn(),
    readFile: vi.fn(),
    writeFile: vi.fn(),
    deletePaths: vi.fn(),
  };
}

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
    ...fileSystemStub(),
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
  it("opens a run member's presented file from the member's own session", async () => {
    function MemberFile() {
      const resources = useConversationResources();
      return (
        <button
          type="button"
          onClick={() =>
            resources?.open({
              kind: 'presented',
              path: 'pf_member',
              name: 'review.md',
              mime: 'text/markdown',
              sessionId: 'reviewer-session',
            })
          }
        >
          Member file
        </button>
      );
    }
    const filesystem = {
      ...fileSystemStub(),
      downloadPresentedFile: vi.fn().mockResolvedValue(blob('# Review', 'text/markdown')),
    };
    render(
      <SessionResources sessionId="room-session" workspace="/home/x" filesystem={filesystem}>
        <MemberFile />
      </SessionResources>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Member file' }));

    await waitFor(() =>
      expect(filesystem.downloadPresentedFile).toHaveBeenCalledWith(
        'reviewer-session',
        'pf_member',
        expect.any(AbortSignal),
      ),
    );
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
  it('reports an image outside the workspace instead of displaying a loading placeholder', () => {
    const download = vi.fn();
    setup(download, '![Screenshot](/home/thor/other-worktree/screenshot.png)');
    expect(screen.getByRole('status')).toHaveTextContent(
      'Screenshot: Image is unavailable in this session’s workspace.',
    );
    expect(screen.queryByText('Loading image…')).not.toBeInTheDocument();
    expect(download).not.toHaveBeenCalled();
  });
  it('loads image bytes asynchronously through the owning session and retries a failed download', async () => {
    let resolve!: (value: Blob) => void;
    const download = vi
      .fn()
      .mockRejectedValueOnce(new Error('Forge is temporarily unreachable'))
      .mockImplementationOnce(() => new Promise<Blob>((done) => (resolve = done)));
    const { unmount } = setup(download, '![Screenshot](docs/landing-forge.png)');
    expect(screen.getByRole('status')).toHaveTextContent('Loading image');
    await screen.findByText(/Forge is temporarily unreachable/);
    fireEvent.click(screen.getByRole('button', { name: 'Retry image' }));
    expect(screen.getByRole('status')).toHaveTextContent('Loading image');
    expect(download).toHaveBeenLastCalledWith(
      'owning-session',
      '/workspace/docs/landing-forge.png',
      expect.any(AbortSignal),
    );
    resolve(blob('image bytes', 'application/octet-stream'));
    expect(await screen.findByRole('img', { name: 'Screenshot' })).toHaveAttribute(
      'src',
      'blob:review-file',
    );
    unmount();
    expect(revokeUrl).toHaveBeenCalledWith('blob:review-file');
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

it('uses the same session panel for websites and external images without invoking the workspace API', async () => {
  const { filesystem } = setup(
    undefined,
    '[Website](https://example.test/page)\n\n[Image](https://example.test/image.png)',
  );
  fireEvent.click(screen.getByRole('button', { name: 'Website' }));
  expect(screen.getByRole('dialog')).toHaveClass('forge-resource-dialog');
  expect(screen.getByTitle('Preview of page')).toHaveAttribute('src', 'https://example.test/page');
  expect(filesystem.downloadFile).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Close', exact: true }));
  fireEvent.click(screen.getByRole('button', { name: 'Image', exact: true }));
  expect(screen.getByRole('dialog')).toHaveClass('forge-resource-dialog');
  expect(screen.getByRole('button', { name: 'Zoom in' })).toBeInTheDocument();
  expect(filesystem.downloadFile).not.toHaveBeenCalled();
});

describe('links to files a stopped or presenting session wrote', () => {
  function render2({
    markdown,
    filesReadable = true,
    presented = [],
    onOpenWorkspaceFile,
  }: {
    markdown: string;
    filesReadable?: boolean;
    presented?: { fileId: string; name: string; mime?: string; path?: string }[];
    onOpenWorkspaceFile?: (path: string) => void;
  }) {
    const filesystem = {
      ...fileSystemStub(),
      downloadFile: vi.fn().mockResolvedValue(blob('live')),
      downloadPresentedFile: vi.fn().mockResolvedValue(blob('# Plan', 'text/markdown')),
    };
    render(
      <SessionResources
        sessionId="session-1"
        filesystem={filesystem}
        presented={presented}
        filesReadable={filesReadable}
        onOpenWorkspaceFile={onOpenWorkspaceFile}
      >
        <MarkdownContent content={markdown} />
      </SessionResources>,
    );
    return filesystem;
  }

  it('opens the durable copy of a presented file from a link to where it was', async () => {
    const filesystem = render2({
      markdown: '[the plan](~/artifacts/plan.md) and **plan.md**',
      filesReadable: false,
      presented: [
        { fileId: 'pf_1', name: 'Plan', mime: 'text/markdown', path: '/home/artifacts/plan.md' },
      ],
    });

    fireEvent.click(screen.getByRole('button', { name: 'the plan' }));
    await waitFor(() =>
      expect(filesystem.downloadPresentedFile).toHaveBeenCalledWith(
        'session-1',
        'pf_1',
        expect.any(AbortSignal),
      ),
    );
    expect(filesystem.downloadFile).not.toHaveBeenCalled();
  });

  it("says a stopped session's own files are gone instead of asking for them", async () => {
    const filesystem = render2({ markdown: '[notes](notes.md)', filesReadable: false });

    fireEvent.click(screen.getByRole('button', { name: 'notes' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'This session has stopped, so the files in its workspace and home are gone.',
    );
    expect(screen.queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument();
    expect(filesystem.downloadFile).not.toHaveBeenCalled();
  });

  it("offers the stopped session's stored copies when its own file is gone", async () => {
    const filesystem = render2({
      markdown: '[plan](plan.md)',
      filesReadable: false,
      presented: [{ fileId: 'pf_1', name: 'Implementation Plan', mime: 'text/markdown' }],
    });

    fireEvent.click(screen.getByRole('button', { name: 'plan' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Implementation Plan' }));

    await waitFor(() =>
      expect(filesystem.downloadPresentedFile).toHaveBeenCalledWith(
        'session-1',
        'pf_1',
        expect.any(AbortSignal),
      ),
    );
  });

  it('reads a home file from a running session', async () => {
    const filesystem = render2({ markdown: '[report](~/out/report.txt)' });

    fireEvent.click(screen.getByRole('button', { name: 'report' }));

    await waitFor(() =>
      expect(filesystem.downloadFile).toHaveBeenCalledWith(
        'session-1',
        '/home/out/report.txt',
        expect.any(AbortSignal),
      ),
    );
  });

  it("hands a live workspace file to the host's own pane instead of the dialog", () => {
    const onOpenWorkspaceFile = vi.fn();
    const filesystem = render2({ markdown: '[notes](docs/notes.md)', onOpenWorkspaceFile });

    fireEvent.click(screen.getByRole('button', { name: 'notes' }));

    expect(onOpenWorkspaceFile).toHaveBeenCalledWith('/workspace/docs/notes.md');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(filesystem.downloadFile).not.toHaveBeenCalled();
  });

  it('lets the host open an external link itself, and falls back to the dialog when it does not', () => {
    const filesystem = { ...fileSystemStub(), downloadFile: vi.fn() };
    const onOpenExternalLink = vi.fn((href: string) => href.includes('localhost'));
    render(
      <SessionResources
        sessionId="session-1"
        filesystem={filesystem}
        onOpenExternalLink={onOpenExternalLink}
      >
        <MarkdownContent content="[dev](http://localhost:5173/docs) and [site](https://example.com/a)" />
      </SessionResources>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'dev' }));
    expect(onOpenExternalLink).toHaveBeenCalledWith('http://localhost:5173/docs');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'site' }));
    expect(onOpenExternalLink).toHaveBeenCalledWith('https://example.com/a');
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  it("keeps a stopped session's file in the dialog, which says it is gone", async () => {
    const onOpenWorkspaceFile = vi.fn();
    render2({ markdown: '[notes](notes.md)', filesReadable: false, onOpenWorkspaceFile });

    fireEvent.click(screen.getByRole('button', { name: 'notes' }));

    expect(onOpenWorkspaceFile).not.toHaveBeenCalled();
    expect(await screen.findByRole('alert')).toHaveTextContent('This session has stopped');
  });
});
