import { describe, it, expect, vi, beforeEach } from 'vitest';
import { compressImage } from './compressImage';

describe('compressImage', () => {
  beforeEach(() => {
    // Mock OffscreenCanvas
    vi.stubGlobal(
      'OffscreenCanvas',
      class {
        width: number;
        height: number;
        constructor(w: number, h: number) {
          this.width = w;
          this.height = h;
        }
        getContext() {
          return { drawImage: vi.fn() };
        }
        convertToBlob() {
          return Promise.resolve(new Blob(['fake'], { type: 'image/jpeg' }));
        }
      },
    );
    // Mock createImageBitmap
    vi.stubGlobal('createImageBitmap', () =>
      Promise.resolve({ width: 100, height: 100, close: vi.fn() }),
    );
    // Mock URL.createObjectURL
    vi.stubGlobal('URL', { createObjectURL: vi.fn(() => 'blob:mock'), revokeObjectURL: vi.fn() });
  });

  it('returns blob and previewUrl', async () => {
    const file = new File([''], 'test.jpg', { type: 'image/jpeg' });
    const result = await compressImage(file);
    expect(result.blob).toBeDefined();
    expect(result.previewUrl).toBe('blob:mock');
  });

  it('throws if canvas context is null', async () => {
    vi.stubGlobal(
      'OffscreenCanvas',
      class {
        getContext() {
          return null;
        }
        convertToBlob() {
          return Promise.resolve(new Blob());
        }
      },
    );
    const file = new File([''], 'test.jpg', { type: 'image/jpeg' });
    await expect(compressImage(file)).rejects.toThrow('Failed to get canvas context');
  });
});
