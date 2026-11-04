import { useEffect, useMemo, useState } from 'react';
import type { ConversationResource } from '../chat';
import type { IFileSystemPort } from './IFileSystemPort';
import { ResourcePreviewContent, loadPreview, type LoadedPreview } from './ResourcePreviewContent';

/** One file from a session, shown in place with the same preview Forge opens files in. */
export function SessionFilePreview({
  sessionId,
  path,
  presented,
  filesystem,
}: {
  sessionId: string;
  /** Absolute path in the session's workspace, or the file's id when it was presented. */
  path: string;
  /** A file the session presented: Forge's stored copy, which outlives the session. */
  presented?: { name: string; mime?: string };
  filesystem: IFileSystemPort;
}) {
  const fileKey = `${sessionId}:${path}`;
  const [read, setRead] = useState<{ key: string; preview?: LoadedPreview; error?: string }>();
  const presentedName = presented?.name;
  const presentedMime = presented?.mime;
  const resource = useMemo<ConversationResource>(
    () =>
      presentedName !== undefined
        ? {
            kind: 'presented',
            path,
            name: presentedName,
            ...(presentedMime ? { mime: presentedMime } : {}),
          }
        : { kind: 'workspace', path, name: path.split('/').at(-1) ?? path },
    [path, presentedName, presentedMime],
  );
  const download =
    resource.kind === 'presented' ? filesystem.downloadPresentedFile : filesystem.downloadFile;

  useEffect(() => {
    if (!download) return;
    const abort = new AbortController();
    let url: string | undefined;
    void download
      .call(filesystem, sessionId, path, abort.signal)
      .then(async (blob) => {
        const preview = await loadPreview(blob, resource.name, resource.mime);
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
  }, [download, fileKey, filesystem, path, resource, sessionId]);

  if (!download) {
    return (
      <p role="alert">
        {resource.kind === 'presented'
          ? 'Presented-file downloads are unavailable on this backend.'
          : 'File downloads are unavailable on this backend.'}
      </p>
    );
  }
  const current = read?.key === fileKey ? read : undefined;
  if (current?.error) return <p role="alert">{current.error}</p>;
  if (!current?.preview) return <p role="status">Loading file…</p>;
  return <ResourcePreviewContent resource={resource} preview={current.preview} />;
}
