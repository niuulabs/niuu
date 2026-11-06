import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { FilePreviewPane } from './FilePreviewPane';
import type { IFileSystemPort } from '../../ports/IFileSystemPort';

describe('FilePreviewPane', () => {
  it('shows the file under its path and hands it to the Files pane', async () => {
    const filesystem: IFileSystemPort = {
      listTree: vi.fn(),
      expandDirectory: vi.fn(),
      readFile: vi.fn(),
      downloadFile: vi.fn().mockResolvedValue(new Blob(['# Paging'], { type: 'text/markdown' })),
      writeFile: vi.fn(),
      deletePaths: vi.fn(),
    };
    const onOpenInFiles = vi.fn();
    render(
      <FilePreviewPane
        sessionId="s1"
        path="/workspace/docs/operator/notes.md"
        filesystem={filesystem}
        onOpenInFiles={onOpenInFiles}
      />,
    );
    const crumbs = screen.getByRole('navigation', { name: 'File path' });
    // The workspace root is left out, as Lexi's crumbs are.
    expect(Array.from(crumbs.children, (part) => part.textContent)).toEqual([
      'docs',
      'operator',
      'notes.md',
    ]);
    expect(await screen.findByRole('heading', { name: 'Paging' })).toBeInTheDocument();
    expect(filesystem.downloadFile).toHaveBeenCalledWith(
      's1',
      '/workspace/docs/operator/notes.md',
      expect.any(AbortSignal),
    );
    fireEvent.click(screen.getByRole('button', { name: /Open in Files/ }));
    expect(onOpenInFiles).toHaveBeenCalled();
  });

  it('keeps a path outside the workspace whole', () => {
    render(
      <FilePreviewPane
        sessionId="s1"
        path="/home/out/report.txt"
        filesystem={{
          listTree: vi.fn(),
          expandDirectory: vi.fn(),
          readFile: vi.fn(),
          downloadFile: vi.fn().mockReturnValue(new Promise(() => {})),
          writeFile: vi.fn(),
          deletePaths: vi.fn(),
        }}
        onOpenInFiles={vi.fn()}
      />,
    );
    expect(screen.getByRole('navigation', { name: 'File path' })).toHaveTextContent(
      'homeoutreport.txt',
    );
  });
});
