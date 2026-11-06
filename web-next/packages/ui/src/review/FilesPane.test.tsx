import { describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { FilesPane } from './FilesPane';
import type { FileTreeNode, IFileSystemPort } from './IFileSystemPort';

const node = (path: string, kind: FileTreeNode['kind'], children?: FileTreeNode[]) => ({
  name: path.split('/').at(-1)!,
  path,
  kind,
  ...(children ? { children } : {}),
});

function filesystem(overrides: Partial<IFileSystemPort> = {}): IFileSystemPort {
  return {
    listTree: vi
      .fn()
      .mockResolvedValue([
        node('/workspace/README.md', 'file'),
        node('/workspace/src', 'directory'),
        node('/workspace/docs', 'directory', [node('/workspace/docs/guide.md', 'file')]),
      ]),
    expandDirectory: vi.fn().mockResolvedValue([node('/workspace/src/app.ts', 'file')]),
    readFile: vi.fn(),
    downloadFile: vi.fn().mockResolvedValue(new Blob(['# Hello'], { type: 'text/markdown' })),
    writeFile: vi.fn(),
    deletePaths: vi.fn(),
    ...overrides,
  };
}

describe('FilesPane', () => {
  it('lists folders first, opens a folder lazily and previews a file under its path', async () => {
    const fs = filesystem();
    render(<FilesPane sessionId="s1" filesystem={fs} />);
    const tree = await screen.findByRole('list');
    const names = within(tree)
      .getAllByRole('button')
      .map((button) => button.textContent);
    expect(names).toEqual(['docs', 'src', 'README.md']);
    expect(screen.getByText('Choose a file to preview it here.')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('files-pane-node-/workspace/src'));
    expect(await screen.findByTestId('files-pane-node-/workspace/src/app.ts')).toBeInTheDocument();
    expect(fs.expandDirectory).toHaveBeenCalledWith('s1', '/workspace/src');

    // A folder the tree already holds opens without asking again.
    fireEvent.click(screen.getByTestId('files-pane-node-/workspace/docs'));
    expect(screen.getByTestId('files-pane-node-/workspace/docs/guide.md')).toBeInTheDocument();
    expect(fs.expandDirectory).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByTestId('files-pane-node-/workspace/README.md'));
    const crumbs = screen.getByRole('navigation', { name: 'File path' });
    expect(crumbs).toHaveTextContent('workspaceREADME.md');
    await waitFor(() => expect(fs.downloadFile).toHaveBeenCalled());

    fireEvent.click(screen.getByTestId('files-pane-node-/workspace/src'));
    expect(screen.queryByTestId('files-pane-node-/workspace/src/app.ts')).not.toBeInTheDocument();
  });

  it('says when the files cannot be listed, and when a folder cannot be opened', async () => {
    render(
      <FilesPane
        sessionId="s1"
        filesystem={filesystem({ listTree: vi.fn().mockRejectedValue(new Error('pod gone')) })}
      />,
    );
    expect(await screen.findByTestId('files-pane-error')).toHaveTextContent(
      "The session's files could not be listed: pod gone",
    );

    render(
      <FilesPane
        sessionId="s2"
        filesystem={filesystem({ expandDirectory: vi.fn().mockRejectedValue(new Error('denied')) })}
      />,
    );
    fireEvent.click(await screen.findByTestId('files-pane-node-/workspace/src'));
    expect(await screen.findByText('Could not open src: denied')).toBeInTheDocument();
  });

  it('says when the workspace is empty', async () => {
    render(
      <FilesPane
        sessionId="s1"
        filesystem={filesystem({ listTree: vi.fn().mockResolvedValue([]) })}
      />,
    );
    expect(await screen.findByText('This workspace is empty.')).toBeInTheDocument();
  });

  it('marks the files the agent touched and opens one from quick open', async () => {
    const { rerender } = render(
      <FilesPane
        sessionId="s1"
        filesystem={filesystem()}
        touched={['/workspace/README.md', '/workspace/src/app.ts']}
      />,
    );
    const readme = await screen.findByTestId('files-pane-node-/workspace/README.md');
    expect(within(readme).getByTitle('The agent touched this file')).toBeInTheDocument();

    rerender(
      <FilesPane
        sessionId="s1"
        filesystem={filesystem()}
        touched={['/workspace/README.md', '/workspace/src/app.ts']}
        quickOpen
      />,
    );
    const dialog = await screen.findByTestId('quick-open');
    fireEvent.change(within(dialog).getByLabelText('Find a file'), { target: { value: 'app' } });
    fireEvent.click(within(dialog).getByRole('option', { name: /app\.ts/ }));
    // The page that holds quick open closes it once a file is picked.
    rerender(
      <FilesPane
        sessionId="s1"
        filesystem={filesystem()}
        touched={['/workspace/README.md', '/workspace/src/app.ts']}
        quickOpen={false}
      />,
    );
    expect(screen.getByRole('navigation', { name: 'File path' })).toHaveTextContent(
      'workspacesrcapp.ts',
    );
  });

  it('opens quick open from its button and hands over to the file manager', async () => {
    const onManage = vi.fn();
    render(<FilesPane sessionId="s1" filesystem={filesystem()} onManage={onManage} />);
    await screen.findByTestId('files-pane-node-/workspace/README.md');
    act(() => {
      fireEvent.click(screen.getByRole('button', { name: 'Quick open a file' }));
    });
    expect(await screen.findByTestId('quick-open')).toBeInTheDocument();
    fireEvent.keyDown(screen.getByLabelText('Find a file'), { key: 'Escape' });
    fireEvent.click(screen.getByRole('button', { name: /Upload, new folder, delete/ }));
    expect(onManage).toHaveBeenCalled();
  });
});
