import { useEffect, useState } from 'react';
import type { ToolImageResource } from './ConversationResources';
import { ImagePreview } from './ImagePreview';
import { useBlobUrl } from '../useBlobUrl';

/** Show the cached thumbnail immediately, then replace it with the original bytes on explicit open. */
export function ToolImagePreview({ resource }: { resource: ToolImageResource }) {
  const [full, setFull] = useState<Blob>();
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);
  const blob = full ?? resource.preview;
  useEffect(() => {
    const controller = new AbortController();
    void resource
      .loadFull(controller.signal)
      .then((blob) => {
        if (!controller.signal.aborted) setFull(blob);
      })
      .catch((failure: unknown) => {
        if (!controller.signal.aborted)
          setError(failure instanceof Error ? failure.message : 'Could not load image.');
      });
    return () => controller.abort();
  }, [resource, attempt]);
  const url = useBlobUrl(blob);
  return (
    <div className="niuu-tool-image-viewer">
      {!full && !error && (
        <p role="status">{blob ? 'Loading full resolution…' : 'Loading image…'}</p>
      )}
      {error && (
        <p role="alert">
          {blob ? 'Showing thumbnail. ' : ''}
          {error}{' '}
          <button
            type="button"
            onClick={() => {
              setError('');
              setAttempt((value) => value + 1);
            }}
          >
            Try again
          </button>
        </p>
      )}
      {url && <ImagePreview src={url} blob={blob} name={resource.name} transferDisabled={!full} />}
    </div>
  );
}
