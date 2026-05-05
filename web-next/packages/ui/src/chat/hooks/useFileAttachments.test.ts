import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useFileAttachments } from './useFileAttachments';

beforeEach(() => {
  Object.defineProperty(URL, 'createObjectURL', {
    value: vi.fn(() => 'blob:mock'),
    writable: true,
  });
  Object.defineProperty(URL, 'revokeObjectURL', { value: vi.fn(), writable: true });
});

/** Build a minimal FileList-like object without DataTransfer */
function makeFileList(...files: File[]): FileList {
  return Object.assign(files, {
    item: (i: number) => files[i] ?? null,
    [Symbol.iterator]: files[Symbol.iterator].bind(files),
  }) as unknown as FileList;
}

function makeDragEvent(files: File[]): React.DragEvent {
  return {
    preventDefault: vi.fn(),
    dataTransfer: { files: makeFileList(...files) },
  } as unknown as React.DragEvent;
}

describe('useFileAttachments', () => {
  it('starts with empty attachments and not dragging', () => {
    const { result } = renderHook(() => useFileAttachments());
    expect(result.current.attachments).toHaveLength(0);
    expect(result.current.isDragging).toBe(false);
  });

  it('addFiles adds non-image files immediately', async () => {
    const { result } = renderHook(() => useFileAttachments());
    const file = new File(['content'], 'notes.txt', { type: 'text/plain' });
    await act(async () => {
      result.current.addFiles(makeFileList(file));
    });
    expect(result.current.attachments).toHaveLength(1);
    expect(result.current.attachments[0].name).toBe('notes.txt');
    expect(result.current.attachments[0].compressed).toBeNull();
  });

  it('addFiles sets previewUrl to null for non-image files', async () => {
    const { result } = renderHook(() => useFileAttachments());
    const file = new File(['data'], 'doc.pdf', { type: 'application/pdf' });
    await act(async () => {
      result.current.addFiles(makeFileList(file));
    });
    expect(result.current.attachments[0].previewUrl).toBeNull();
  });

  it('removeAttachment removes by id', async () => {
    const { result } = renderHook(() => useFileAttachments());
    const file = new File(['content'], 'notes.txt', { type: 'text/plain' });
    await act(async () => {
      result.current.addFiles(makeFileList(file));
    });
    const id = result.current.attachments[0].id;
    act(() => {
      result.current.removeAttachment(id);
    });
    expect(result.current.attachments).toHaveLength(0);
  });

  it('clearAttachments removes all', async () => {
    const { result } = renderHook(() => useFileAttachments());
    const files = [
      new File(['a'], 'a.txt', { type: 'text/plain' }),
      new File(['b'], 'b.txt', { type: 'text/plain' }),
    ];
    await act(async () => {
      result.current.addFiles(makeFileList(...files));
    });
    act(() => {
      result.current.clearAttachments();
    });
    expect(result.current.attachments).toHaveLength(0);
  });

  it('handleDragOver sets isDragging=true', () => {
    const { result } = renderHook(() => useFileAttachments());
    act(() => {
      result.current.handleDragOver({ preventDefault: vi.fn() } as unknown as React.DragEvent);
    });
    expect(result.current.isDragging).toBe(true);
  });

  it('handleDragLeave sets isDragging=false', () => {
    const { result } = renderHook(() => useFileAttachments());
    act(() => {
      result.current.handleDragOver({ preventDefault: vi.fn() } as unknown as React.DragEvent);
    });
    act(() => {
      result.current.handleDragLeave({ preventDefault: vi.fn() } as unknown as React.DragEvent);
    });
    expect(result.current.isDragging).toBe(false);
  });

  it('handleDrop adds dropped files', async () => {
    const { result } = renderHook(() => useFileAttachments());
    const file = new File(['content'], 'dropped.txt', { type: 'text/plain' });
    await act(async () => {
      result.current.handleDrop(makeDragEvent([file]));
    });
    expect(result.current.attachments).toHaveLength(1);
    expect(result.current.isDragging).toBe(false);
  });

  it('handleDrop with no files does not add attachments', async () => {
    const { result } = renderHook(() => useFileAttachments());
    await act(async () => {
      result.current.handleDrop(makeDragEvent([]));
    });
    expect(result.current.attachments).toHaveLength(0);
  });

  it('handlePaste ignores non-image clipboard items', () => {
    const { result } = renderHook(() => useFileAttachments());
    const pasteEvent = {
      clipboardData: {
        items: [{ kind: 'string', type: 'text/plain', getAsFile: () => null }],
      },
      preventDefault: vi.fn(),
    } as unknown as React.ClipboardEvent;
    act(() => {
      result.current.handlePaste(pasteEvent);
    });
    expect(result.current.attachments).toHaveLength(0);
  });
});
