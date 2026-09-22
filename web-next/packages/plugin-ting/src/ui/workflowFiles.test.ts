import { afterEach, describe, expect, it, vi } from 'vitest';
import type { WorkflowExport } from '../ports';
import { downloadWorkflowFile, readWorkflowFile } from './workflowFiles';

describe('readWorkflowFile', () => {
  it('resolves the payload after the data-url comma', async () => {
    const file = new File(['name: Workflow 1'], 'workflow.yaml', { type: 'text/yaml' });
    const content = await readWorkflowFile(file);
    expect(content).not.toContain('data:');
    expect(atob(content)).toBe('name: Workflow 1');
  });

  it('rejects with the reader error when the read fails', async () => {
    class FailingFileReader {
      result: unknown = null;
      error = new Error('disk error');
      onerror: (() => void) | null = null;
      onload: (() => void) | null = null;
      readAsDataURL() {
        queueMicrotask(() => this.onerror?.());
      }
    }
    vi.stubGlobal('FileReader', FailingFileReader);
    try {
      const file = new File(['x'], 'broken.yaml');
      await expect(readWorkflowFile(file)).rejects.toThrow('disk error');
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('rejects with a default error when the reader reports no error object', async () => {
    class NoErrorFileReader {
      result: unknown = null;
      error: unknown = null;
      onerror: (() => void) | null = null;
      onload: (() => void) | null = null;
      readAsDataURL() {
        queueMicrotask(() => this.onerror?.());
      }
    }
    vi.stubGlobal('FileReader', NoErrorFileReader);
    try {
      const file = new File(['x'], 'broken.yaml');
      await expect(readWorkflowFile(file)).rejects.toThrow('Could not read broken.yaml.');
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('rejects when the reader result is not a string', async () => {
    class BinaryFileReader {
      result: unknown = new ArrayBuffer(1);
      error: unknown = null;
      onerror: (() => void) | null = null;
      onload: (() => void) | null = null;
      readAsDataURL() {
        queueMicrotask(() => this.onload?.());
      }
    }
    vi.stubGlobal('FileReader', BinaryFileReader);
    try {
      const file = new File(['x'], 'weird.yaml');
      await expect(readWorkflowFile(file)).rejects.toThrow('Could not encode weird.yaml.');
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('returns the raw reader result when it contains no comma', async () => {
    class NoCommaFileReader {
      result: unknown = 'plain-text-result';
      error: unknown = null;
      onerror: (() => void) | null = null;
      onload: (() => void) | null = null;
      readAsDataURL() {
        queueMicrotask(() => this.onload?.());
      }
    }
    vi.stubGlobal('FileReader', NoCommaFileReader);
    try {
      const file = new File(['x'], 'plain.yaml');
      await expect(readWorkflowFile(file)).resolves.toBe('plain-text-result');
    } finally {
      vi.unstubAllGlobals();
    }
  });
});

describe('downloadWorkflowFile', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('creates an object URL, clicks a download anchor and revokes the URL', () => {
    const createObjectURL = vi.fn().mockReturnValue('blob:workflow-export');
    const revokeObjectURL = vi.fn();
    vi.stubGlobal('URL', { ...URL, createObjectURL, revokeObjectURL });
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});

    const file: WorkflowExport = {
      data: new Blob(['name: Workflow 1']),
      filename: 'workflow-1.yaml',
      mediaType: 'text/yaml',
    };
    downloadWorkflowFile(file);

    expect(createObjectURL).toHaveBeenCalledWith(file.data);
    expect(click).toHaveBeenCalledTimes(1);
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:workflow-export');

    vi.unstubAllGlobals();
  });
});
