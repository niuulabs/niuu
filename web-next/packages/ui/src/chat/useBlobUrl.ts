import { useMemo, useSyncExternalStore } from 'react';

function blobUrlStore(blob: Blob | undefined) {
  let url = '';
  return {
    snapshot: () => url,
    subscribe: (changed: () => void) => {
      if (blob) {
        url = URL.createObjectURL(blob);
        changed();
      }
      return () => {
        if (url) URL.revokeObjectURL(url);
        url = '';
      };
    },
  };
}

/** Allocate outside render and revoke when the view releases its bytes. */
export function useBlobUrl(blob: Blob | undefined): string {
  const store = useMemo(() => blobUrlStore(blob), [blob]);
  return useSyncExternalStore(store.subscribe, store.snapshot, () => '');
}
