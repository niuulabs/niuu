import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { SessionFilesWorkspace } from './SessionFilesWorkspace';
import type { FileTreeNode, IFileSystemPort } from '../ports/IFileSystemPort';

vi.mock('./FileTree/FileViewer', () => ({
  FileViewer: ({
    path,
    content,
    error,
    isLoading,
    onClose,
  }: {
    path: string;
    content: string;
    error?: string;
    isLoading?: boolean;
    onClose?: () => void;
  }) => (
    <div data-testid="mock-file-viewer">
      <div>{path}</div>
      <div>{content}</div>
      {error ? <div>{error}</div> : null}
      {isLoading ? <div>loading</div> : null}
      <button type="button" onClick={onClose}>
        close viewer
      </button>
    </div>
  ),
}));

const TREE: FileTreeNode[] = [
  {
    name: 'src',
    path: '/workspace/src',
    kind: 'directory',
    children: [
      {
        name: 'index.ts',
        path: '/workspace/src/index.ts',
        kind: 'file',
        size: 24,
      },
    ],
  },
  {
    name: 'README.md',
    path: '/workspace/README.md',
    kind: 'file',
    size: 18,
  },
  {
    name: 'secrets.env',
    path: '/workspace/secrets.env',
    kind: 'file',
    size: 12,
    isSecret: true,
  },
  {
    name: 'shared',
    path: '/mnt/shared',
    kind: 'directory',
    mountName: 'shared',
    children: [
      {
        name: 'notes.txt',
        path: '/mnt/shared/notes.txt',
        kind: 'file',
        size: 9,
        mountName: 'shared',
      },
    ],
  },
];

function createFilesystem(): IFileSystemPort {
  return {
    listTree: vi.fn().mockResolvedValue(TREE),
    expandDirectory: vi.fn().mockResolvedValue([]),
    readFile: vi.fn(async (_sessionId: string, path: string) => {
      if (path === '/workspace/broken.txt') {
        throw new Error('Cannot open file');
      }
      return `contents:${path}`;
    }),
    writeFile: vi.fn().mockResolvedValue(undefined),
    deletePaths: vi.fn().mockResolvedValue(undefined),
  };
}

function renderWorkspace(filesystem = createFilesystem()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return {
    filesystem,
    ...render(
      <QueryClientProvider client={client}>
        <SessionFilesWorkspace sessionId="sess-1" filesystem={filesystem} />
      </QueryClientProvider>,
    ),
  };
}

function makeTextFile(name: string, content: string): File {
  const file = new File([content], name, { type: 'text/plain' });
  Object.defineProperty(file, 'text', {
    value: () => Promise.resolve(content),
  });
  return file;
}

describe('SessionFilesWorkspace', () => {
  const originalCreateObjectUrl = URL.createObjectURL;
  const originalRevokeObjectUrl = URL.revokeObjectURL;

  beforeEach(() => {
    URL.createObjectURL = vi.fn(() => 'blob:download');
    URL.revokeObjectURL = vi.fn();
  });

  afterEach(() => {
    URL.createObjectURL = originalCreateObjectUrl;
    URL.revokeObjectURL = originalRevokeObjectUrl;
  });

  it('navigates directories, opens files, and switches roots', async () => {
    const { filesystem } = renderWorkspace();

    await waitFor(() =>
      expect(screen.getByTestId('file-browser-row-/workspace/src')).toBeVisible(),
    );
    expect(screen.getByText('shared')).toBeVisible();

    fireEvent.doubleClick(screen.getByTestId('file-browser-row-/workspace/src'));
    await waitFor(() =>
      expect(screen.getByTestId('file-browser-row-/workspace/src/index.ts')).toBeVisible(),
    );

    fireEvent.click(screen.getAllByRole('button', { name: 'workspace' })[1]!);
    await waitFor(() =>
      expect(screen.getByTestId('file-browser-row-/workspace/README.md')).toBeVisible(),
    );

    fireEvent.doubleClick(screen.getByTestId('file-browser-row-/workspace/README.md'));
    await waitFor(() =>
      expect(filesystem.readFile).toHaveBeenCalledWith('sess-1', '/workspace/README.md'),
    );
    expect(screen.getByTestId('mock-file-viewer')).toHaveTextContent(
      'contents:/workspace/README.md',
    );

    fireEvent.click(screen.getByRole('button', { name: 'close viewer' }));
    await waitFor(() => expect(screen.queryByTestId('mock-file-viewer')).not.toBeInTheDocument());

    fireEvent.doubleClick(screen.getByTestId('file-browser-row-/workspace/secrets.env'));
    expect(filesystem.readFile).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole('button', { name: 'shared' }));
    await waitFor(() =>
      expect(screen.getByTestId('file-browser-row-/mnt/shared/notes.txt')).toBeVisible(),
    );
  });

  it('downloads selected files, uploads new files, creates folders, and deletes selections', async () => {
    const { filesystem, container } = renderWorkspace();
    const downloadClick = vi
      .spyOn(HTMLAnchorElement.prototype, 'click')
      .mockImplementation(() => {});

    await waitFor(() =>
      expect(screen.getByTestId('file-browser-row-/workspace/README.md')).toBeVisible(),
    );

    fireEvent.click(screen.getByTestId('file-browser-row-/workspace/README.md'));
    fireEvent.click(screen.getByTestId('file-browser-row-/workspace/secrets.env'), {
      ctrlKey: true,
    });
    expect(screen.getAllByText('2 selected')).toHaveLength(2);

    fireEvent.click(screen.getByRole('button', { name: 'download' }));
    fireEvent.click(screen.getAllByRole('button', { name: /^download$/ })[1]!);

    await waitFor(() =>
      expect(filesystem.readFile).toHaveBeenCalledWith('sess-1', '/workspace/README.md'),
    );
    expect(filesystem.readFile).not.toHaveBeenCalledWith('sess-1', '/workspace/secrets.env');
    expect(URL.createObjectURL).toHaveBeenCalledTimes(1);
    expect(downloadClick).toHaveBeenCalledTimes(1);
    expect(URL.revokeObjectURL).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole('button', { name: 'create folder' }));
    fireEvent.change(screen.getByPlaceholderText('folder-name'), { target: { value: 'docs' } });
    fireEvent.click(screen.getByRole('button', { name: 'create' }));
    await waitFor(() =>
      expect(filesystem.writeFile).toHaveBeenCalledWith('sess-1', '/workspace/docs/.keep', ''),
    );

    const fileInput = container.querySelector('input[type="file"]');
    expect(fileInput).not.toBeNull();
    fireEvent.change(fileInput!, {
      target: {
        files: [makeTextFile('draft.txt', 'hello')],
      },
    });
    await waitFor(() =>
      expect(filesystem.writeFile).toHaveBeenCalledWith('sess-1', '/workspace/draft.txt', 'hello'),
    );
    expect(screen.getByText('uploads (1/1)')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'clear' }));
    await waitFor(() => expect(screen.queryByText(/uploads \(/)).not.toBeInTheDocument());

    fireEvent.click(screen.getByTestId('file-browser-row-/workspace/README.md'));
    fireEvent.click(screen.getByRole('button', { name: 'delete' }));
    fireEvent.click(screen.getAllByRole('button', { name: /^delete$/ })[1]!);
    await waitFor(() =>
      expect(filesystem.deletePaths).toHaveBeenCalledWith('sess-1', ['/workspace/README.md']),
    );

    downloadClick.mockRestore();
  });

  it('shows drop state and reports filesystem errors', async () => {
    const filesystem = createFilesystem();
    filesystem.writeFile = vi.fn().mockRejectedValue(new Error('Upload failed'));
    filesystem.deletePaths = vi.fn().mockRejectedValue(new Error('Cannot delete'));
    filesystem.readFile = vi.fn().mockRejectedValue(new Error('Cannot open file'));
    const { container } = renderWorkspace(filesystem);

    await waitFor(() =>
      expect(screen.getByTestId('file-browser-row-/workspace/README.md')).toBeVisible(),
    );

    fireEvent.dragOver(screen.getByTestId('session-files-workspace'));
    expect(screen.getByText('drop files to upload')).toBeVisible();

    fireEvent.drop(screen.getByTestId('session-files-workspace'), {
      dataTransfer: {
        files: [makeTextFile('broken.txt', 'bad')],
      },
    });
    await waitFor(() =>
      expect(filesystem.writeFile).toHaveBeenCalledWith('sess-1', '/workspace/broken.txt', 'bad'),
    );
    await waitFor(() => expect(screen.getByText('broken.txt')).toBeVisible());

    fireEvent.doubleClick(screen.getByTestId('file-browser-row-/workspace/README.md'));
    await waitFor(() =>
      expect(screen.getByTestId('mock-file-viewer')).toHaveTextContent('Cannot open file'),
    );

    fireEvent.click(screen.getByRole('button', { name: 'close viewer' }));
    fireEvent.click(screen.getByTestId('file-browser-row-/workspace/README.md'));
    fireEvent.click(screen.getByRole('button', { name: 'delete' }));
    fireEvent.click(screen.getAllByRole('button', { name: /^delete$/ })[1]!);
    await waitFor(() => expect(screen.getByText('Cannot delete')).toBeVisible());

    fireEvent.click(screen.getByRole('button', { name: 'dismiss' }));
    await waitFor(() => expect(screen.queryByText('Cannot delete')).not.toBeInTheDocument());

    const overlayTarget = container.querySelector('[data-testid="session-files-workspace"]');
    fireEvent.dragLeave(overlayTarget!, { relatedTarget: document.body });
    await waitFor(() => expect(screen.queryByText('drop files to upload')).not.toBeInTheDocument());
  });
});
