import { useEffect, useMemo, useState } from 'react';
import type { ConversationResource } from '../chat';
import type { IFileSystemPort } from './IFileSystemPort';
import { ResourcePreviewContent, loadPreview, type LoadedPreview } from './ResourcePreviewContent';

/** One file from a session, shown in place with the same preview Forge opens files in. */
export function SessionFilePreview({
  sessionId,
  path,
  filesystem,
}: {
  sessionId: string;
  /** Absolute path in the session's workspace. */
  path: string;
  filesystem: IFileSystemPort;
}) {
  const fileKey = `${sessionId}:${path}`;
  const [read, setRead] = useState<{ key: string; preview?: LoadedPreview; error?: string }>();
  const resource = useMemo<ConversationResource>(
    () => ({ kind: 'workspace', path, name: path.split('/').at(-1) ?? path }),
    [path],
  );

  useEffect(() => {
    if (!filesystem.downloadFile) return;
    const abort = new AbortController();
    let url: string | undefined;
    void filesystem
      .downloadFile(sessionId, path, abort.signal)
      .then(async (blob) => {
        const preview = await loadPreview(blob, resource.name);
        if (abort.signal.aborted) {
          URL.revokeObjectURL(preview.url);
          return;
        }
        url = preview.url;
        setRead({ key: fileKey, preview });
      })
      .catch((failure: unknown) => {
        if (abort.signal.aborted) return;
        const error = failure instanceof Error ? failure.message : 'Could not load file';
        setRead({ key: fileKey, error });
      });
    return () => {
      abort.abort();
      if (url) URL.revokeObjectURL(url);
    };
  }, [fileKey, filesystem, path, resource.name, sessionId]);

  if (!filesystem.downloadFile) {
    return <p role="alert">File downloads are unavailable on this backend.</p>;
  }
  const current = read?.key === fileKey ? read : undefined;
  if (current?.error) return <p role="alert">{current.error}</p>;
  if (!current?.preview) return <p role="status">Loading file…</p>;
  return <ResourcePreviewContent resource={resource} preview={current.preview} />;
}
