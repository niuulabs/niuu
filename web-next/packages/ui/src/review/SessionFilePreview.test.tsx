import { render, screen } from '@testing-library/react';
import { Blob as NodeBlob } from 'node:buffer';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { IFileSystemPort } from './IFileSystemPort';
import { SessionFilePreview } from './SessionFilePreview';

function filesystem(downloadFile?: IFileSystemPort['downloadFile']): IFileSystemPort {
  return {
    listTree: vi.fn(),
    expandDirectory: vi.fn(),
    readFile: vi.fn(),
    writeFile: vi.fn(),
    deletePaths: vi.fn(),
    downloadFile,
  };
}

describe('SessionFilePreview', () => {
  beforeEach(() => {
    vi.stubGlobal(
      'URL',
      class extends URL {
        static createObjectURL = vi.fn(() => 'blob:file');
        static revokeObjectURL = vi.fn();
      },
    );
  });
  afterEach(() => vi.unstubAllGlobals());

  it('shows a workspace file in place', async () => {
    const download = vi
      .fn()
      .mockResolvedValue(new NodeBlob(['# Report'], { type: 'text/markdown' }) as unknown as Blob);
    render(
      <SessionFilePreview
        sessionId="s1"
        path="/workspace/report/quality.md"
        filesystem={filesystem(download)}
      />,
    );

    expect(await screen.findByRole('heading', { name: 'Report' })).toBeInTheDocument();
    expect(download.mock.calls[0]?.slice(0, 2)).toEqual(['s1', '/workspace/report/quality.md']);
  });

  it('says why a file could not be read', async () => {
    render(
      <SessionFilePreview
        sessionId="s1"
        path="/workspace/gone.pdf"
        filesystem={filesystem(vi.fn().mockRejectedValue(new Error('Session is stopped')))}
      />,
    );

    expect(await screen.findByRole('alert')).toHaveTextContent('Session is stopped');
  });

  it('says when this backend cannot download files', async () => {
    render(<SessionFilePreview sessionId="s1" path="/workspace/a" filesystem={filesystem()} />);

    expect(await screen.findByRole('alert')).toHaveTextContent('unavailable');
  });
});
