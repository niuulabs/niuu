import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { FileTree } from './FileTree';
import { FileViewer } from './FileViewer';
import type { FileTreeNode } from '../../ports/IFileSystemPort';

// ---------------------------------------------------------------------------
// Shiki mock — avoid heavy language grammar loading in unit tests
// ---------------------------------------------------------------------------

vi.mock('shiki', () => ({
  codeToHtml: vi
    .fn()
    .mockResolvedValue('<pre><code data-testid="shiki-output">highlighted code</code></pre>'),
}));

// ---------------------------------------------------------------------------
// Seed data
// ---------------------------------------------------------------------------

const SEED_NODES: FileTreeNode[] = [
  {
    name: 'src',
    path: '/workspace/src',
    kind: 'directory',
    children: [
      { name: 'main.ts', path: '/workspace/src/main.ts', kind: 'file', size: 1_024 },
      { name: 'utils.ts', path: '/workspace/src/utils.ts', kind: 'file', size: 512 },
    ],
  },
  {
    name: 'package.json',
    path: '/workspace/package.json',
    kind: 'file',
    size: 2_048,
  },
  {
    name: 'secrets',
    path: '/mnt/secrets',
    kind: 'directory',
    mountName: 'my-secret',
    isSecret: true,
    children: [
      {
        name: 'API_KEY',
        path: '/mnt/secrets/API_KEY',
        kind: 'file',
        isSecret: true,
      },
    ],
  },
];

// ---------------------------------------------------------------------------
// FileTree tests
// ---------------------------------------------------------------------------

describe('FileTree', () => {
  it('renders an empty state when nodes array is empty', () => {
    render(<FileTree nodes={[]} />);
    expect(screen.getByTestId('filetree-empty')).toBeInTheDocument();
  });

  it('renders root-level nodes', () => {
    render(<FileTree nodes={SEED_NODES} />);
    expect(screen.getByTestId('filetree-root')).toBeInTheDocument();
    expect(screen.getByText('src')).toBeInTheDocument();
    expect(screen.getByText('package.json')).toBeInTheDocument();
  });

  it('shows children of depth-0 directories on initial render', () => {
    render(<FileTree nodes={SEED_NODES} />);
    // Top-level dirs start expanded.
    expect(screen.getByText('main.ts')).toBeInTheDocument();
    expect(screen.getByText('utils.ts')).toBeInTheDocument();
  });

  it('collapses directory children when clicked', () => {
    render(<FileTree nodes={SEED_NODES} />);
    // src starts expanded — click to collapse
    fireEvent.click(screen.getByText('src'));
    expect(screen.queryByText('main.ts')).not.toBeInTheDocument();
  });

  it('re-expands directory children on second click', () => {
    render(<FileTree nodes={SEED_NODES} />);
    fireEvent.click(screen.getByText('src')); // collapse
    fireEvent.click(screen.getByText('src')); // re-expand
    expect(screen.getByText('main.ts')).toBeInTheDocument();
  });

  it('calls onOpenFile when a non-secret file is clicked', () => {
    const onOpenFile = vi.fn();
    render(<FileTree nodes={SEED_NODES} onOpenFile={onOpenFile} />);
    fireEvent.click(screen.getByText('package.json'));
    expect(onOpenFile).toHaveBeenCalledWith('/workspace/package.json', undefined);
  });

  it('does NOT call onOpenFile for secret files', () => {
    const onOpenFile = vi.fn();
    render(<FileTree nodes={SEED_NODES} onOpenFile={onOpenFile} />);
    // secrets dir starts expanded at depth 0 — API_KEY is already visible
    const apiKeyEl = screen.getByText('API_KEY');
    fireEvent.click(apiKeyEl);
    expect(onOpenFile).not.toHaveBeenCalled();
  });

  it('shows "secret" badge on secret nodes', () => {
    render(<FileTree nodes={SEED_NODES} />);
    expect(screen.getByTestId('filetree-secret-badge-/mnt/secrets')).toBeInTheDocument();
  });

  it('shows mount boundary label for mount-root nodes', () => {
    render(<FileTree nodes={SEED_NODES} />);
    expect(screen.getByTestId('filetree-mount-my-secret')).toBeInTheDocument();
    expect(screen.getByText(/mount: my-secret/)).toBeInTheDocument();
  });

  it('highlights the active path', () => {
    render(<FileTree nodes={SEED_NODES} activePath="/workspace/package.json" />);
    const node = screen.getByTestId('filetree-node-/workspace/package.json');
    expect(node).toHaveAttribute('aria-selected', 'true');
  });

  it('shows (empty) label for empty directories (depth-0 auto-expanded)', () => {
    const nodes: FileTreeNode[] = [
      { name: 'dist', path: '/workspace/dist', kind: 'directory', children: [] },
    ];
    render(<FileTree nodes={nodes} />);
    // Top-level dirs start expanded — (empty) label is visible immediately.
    expect(screen.getByTestId('filetree-empty-dir-/workspace/dist')).toBeInTheDocument();
  });

  it('supports keyboard navigation with Enter', () => {
    const onOpenFile = vi.fn();
    render(<FileTree nodes={SEED_NODES} onOpenFile={onOpenFile} />);
    const node = screen.getByText('package.json').closest('[role="button"]')!;
    fireEvent.keyDown(node, { key: 'Enter' });
    expect(onOpenFile).toHaveBeenCalledWith('/workspace/package.json', undefined);
  });

  it('formats file sizes correctly', () => {
    render(<FileTree nodes={SEED_NODES} />);
    // package.json is 2048 bytes = 2.0KB
    expect(screen.getByText('2.0KB')).toBeInTheDocument();
  });

  it('shows file trees without mounts (no mount boundary labels)', () => {
    const plain: FileTreeNode[] = [
      { name: 'README.md', path: '/workspace/README.md', kind: 'file', size: 100 },
    ];
    render(<FileTree nodes={plain} />);
    expect(screen.queryByText(/mount:/)).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// FileViewer tests
// ---------------------------------------------------------------------------

describe('FileViewer', () => {
  it('renders the file path in the header', () => {
    render(<FileViewer path="/workspace/src/main.ts" content="const x = 1;" />);
    expect(screen.getByTestId('file-viewer-path')).toHaveTextContent('/workspace/src/main.ts');
  });

  it('detects language from file extension', () => {
    render(<FileViewer path="/workspace/src/main.ts" content="const x = 1;" />);
    expect(screen.getByText('typescript')).toBeInTheDocument();
  });

  it('shows loading indicator while isLoading=true', () => {
    render(<FileViewer path="/workspace/src/main.ts" content="" isLoading />);
    expect(screen.getByTestId('file-viewer-loading')).toBeInTheDocument();
  });

  it('shows error message when error is provided', () => {
    render(<FileViewer path="/workspace/src/main.ts" content="" error="file not found" />);
    expect(screen.getByTestId('file-viewer-error')).toHaveTextContent('file not found');
  });

  it('calls onClose when close button is clicked', () => {
    const onClose = vi.fn();
    render(<FileViewer path="/workspace/src/main.ts" content="x" onClose={onClose} />);
    fireEvent.click(screen.getByTestId('file-viewer-close'));
    expect(onClose).toHaveBeenCalled();
  });

  it('renders highlighted content via Shiki', async () => {
    render(<FileViewer path="/workspace/src/main.ts" content="const x = 1;" />);
    await waitFor(() => expect(screen.getByTestId('file-viewer-highlighted')).toBeInTheDocument());
  });

  it('falls back to plain text when Shiki throws', async () => {
    const { codeToHtml } = await import('shiki');
    vi.mocked(codeToHtml).mockRejectedValueOnce(new Error('shiki error'));

    render(<FileViewer path="/workspace/src/main.ts" content="const x = 1;" />);
    await waitFor(() => expect(screen.getByTestId('file-viewer-plain')).toBeInTheDocument());
    expect(screen.getByTestId('file-viewer-highlight-warning')).toBeInTheDocument();
  });

  it('does not render a close button when onClose is not provided', () => {
    render(<FileViewer path="/workspace/src/main.ts" content="x" />);
    expect(screen.queryByTestId('file-viewer-close')).not.toBeInTheDocument();
  });
});
