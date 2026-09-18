import { renderHook } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { useBlobUrl } from './useBlobUrl';
it('allocates only subscribed blobs and releases replaced and unmounted URLs', () => {
  const create = vi
    .spyOn(URL, 'createObjectURL')
    .mockReturnValueOnce('blob:first')
    .mockReturnValueOnce('blob:second');
  const revoke = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});
  const { result, rerender, unmount } = renderHook(
    ({ blob }: { blob?: Blob }) => useBlobUrl(blob),
    { initialProps: {} as { blob?: Blob } },
  );
  expect(result.current).toBe('');
  expect(create).not.toHaveBeenCalled();
  rerender({ blob: new Blob(['a']) });
  expect(result.current).toBe('blob:first');
  rerender({ blob: new Blob(['b']) });
  expect(result.current).toBe('blob:second');
  expect(revoke).toHaveBeenCalledWith('blob:first');
  unmount();
  expect(revoke).toHaveBeenCalledWith('blob:second');
});
