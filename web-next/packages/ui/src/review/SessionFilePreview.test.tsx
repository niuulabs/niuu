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

  it("shows a presented file from Forge's stored copy, by its id", async () => {
    const downloadFile = vi.fn();
    const downloadPresentedFile = vi
      .fn()
      .mockResolvedValue(
        new NodeBlob(['%PDF-1.4'], { type: 'application/pdf' }) as unknown as Blob,
      );
    render(
      <SessionFilePreview
        sessionId="s1"
        path="pf_1"
        presented={{ name: 'Summary', mime: 'application/pdf' }}
        filesystem={{ ...filesystem(downloadFile), downloadPresentedFile }}
      />,
    );

    expect(await screen.findByTitle('Summary')).toBeInTheDocument();
    expect(downloadPresentedFile.mock.calls[0]?.slice(0, 2)).toEqual(['s1', 'pf_1']);
    expect(downloadFile).not.toHaveBeenCalled();
  });

  it('says when this backend cannot download presented files', async () => {
    render(
      <SessionFilePreview
        sessionId="s1"
        path="pf_1"
        presented={{ name: 'Summary' }}
        filesystem={filesystem(vi.fn())}
      />,
    );

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Presented-file downloads are unavailable',
    );
  });

  it('says when this backend cannot download files', async () => {
    render(<SessionFilePreview sessionId="s1" path="/workspace/a" filesystem={filesystem()} />);

    expect(await screen.findByRole('alert')).toHaveTextContent('unavailable');
  });
});
