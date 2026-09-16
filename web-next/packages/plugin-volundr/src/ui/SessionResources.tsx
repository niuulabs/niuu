import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import {
  ConversationResourceProvider,
  Dialog,
  DialogContent,
  MarkdownContent,
  type ConversationResource,
} from '@niuulabs/ui';
import type { IFileSystemPort } from '../ports/IFileSystemPort';
import { resolveSessionResource } from '../domain/sessionResources';
import './SessionResources.css';

const INLINE_TEXT_LIMIT = 2 * 1024 * 1024;
type Preview = {
  url: string;
  blob: Blob;
  text?: string;
  kind: 'image' | 'pdf' | 'text' | 'download';
};

export function SessionResources({
  sessionId,
  workspace,
  filesystem,
  children,
}: {
  sessionId: string;
  workspace?: string;
  filesystem: IFileSystemPort;
  children: ReactNode;
}) {
  const [selected, setSelected] = useState<ConversationResource | null>(null);
  const [preview, setPreview] = useState<Preview>();
  const [error, setError] = useState<string>();
  const load = useCallback(
    (resource: ConversationResource, signal: AbortSignal) => {
      if (resource.kind === 'presented') {
        if (!filesystem.downloadPresentedFile)
          return Promise.reject(
            new Error('Presented-file downloads are unavailable on this backend.'),
          );
        return filesystem.downloadPresentedFile(sessionId, resource.path, signal);
      }
      if (!filesystem.downloadFile)
        return Promise.reject(new Error('File downloads are unavailable on this backend.'));
      return filesystem.downloadFile(sessionId, resource.path, signal);
    },
    [filesystem, sessionId],
  );
  const open = useCallback((resource: ConversationResource) => {
    setPreview(undefined);
    setError(undefined);
    setSelected(resource);
  }, []);
  const port = useMemo(
    () => ({
      resolve: (href: string) => resolveSessionResource(href, workspace),
      load,
      open,
    }),
    [workspace, load, open],
  );

  const previewPort = useMemo(
    () => ({
      ...port,
      resolve: (href: string) => {
        const directory =
          selected?.kind === 'workspace'
            ? selected.path.slice(0, selected.path.lastIndexOf('/') + 1)
            : '';
        const isRelative = !/^(?:[a-z][a-z\d+.-]*:|\/|#)/i.test(href);
        return port.resolve(directory && isRelative ? `${directory}${href}` : href);
      },
    }),
    [port, selected],
  );

  useEffect(() => {
    if (!selected) return;
    const abort = new AbortController();
    let url: string | undefined;
    void load(selected, abort.signal)
      .then(async (blob) => {
        const mime = selected.mime || blob.type;
        const ext = selected.name.split('.').at(-1)?.toLowerCase() ?? '';
        const kind =
          mime.startsWith('image/') ||
          ['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'avif'].includes(ext)
            ? 'image'
            : mime === 'application/pdf' || ext === 'pdf'
              ? 'pdf'
              : mime.startsWith('text/') ||
                  [
                    'md',
                    'txt',
                    'json',
                    'yaml',
                    'yml',
                    'ts',
                    'tsx',
                    'js',
                    'jsx',
                    'py',
                    'swift',
                    'css',
                    'html',
                    'csv',
                    'log',
                    'sh',
                    'sql',
                    'toml',
                  ].includes(ext)
                ? 'text'
                : 'download';
        const text =
          kind === 'text' && blob.size <= INLINE_TEXT_LIMIT ? await blob.text() : undefined;
        if (abort.signal.aborted) return;
        url = URL.createObjectURL(
          kind === 'pdf' && blob.type !== 'application/pdf'
            ? new Blob([blob], { type: 'application/pdf' })
            : blob,
        );
        setPreview({ url, blob, text, kind });
      })
      .catch((error: unknown) => {
        if (!abort.signal.aborted)
          setError(error instanceof Error ? error.message : 'Could not load file');
      });
    return () => {
      abort.abort();
      if (url) URL.revokeObjectURL(url);
    };
  }, [selected, load]);

  return (
    <ConversationResourceProvider port={port}>
      {children}
      <Dialog
        open={selected !== null}
        onOpenChange={(open) => {
          if (!open) setSelected(null);
        }}
      >
        <DialogContent
          title={selected?.name ?? 'File preview'}
          description={selected?.kind === 'workspace' ? selected.path : 'Delivered by this session'}
        >
          <div className="forge-resource-preview">
            {error && <p role="alert">{error}</p>}
            {!preview && !error && <p role="status">Loading file…</p>}
            {preview && (
              <>
                <a
                  href={preview.url}
                  download={selected?.name}
                  className="forge-resource-preview__download"
                >
                  Download {selected?.name}
                </a>
                {preview.kind === 'image' && <img src={preview.url} alt={selected?.name} />}
                {preview.kind === 'pdf' && <iframe title={selected?.name} src={preview.url} />}
                {preview.text !== undefined &&
                  (selected?.name.endsWith('.md') ? (
                    <ConversationResourceProvider port={previewPort}>
                      <MarkdownContent content={preview.text} />
                    </ConversationResourceProvider>
                  ) : (
                    <pre>{preview.text}</pre>
                  ))}
                {preview.kind === 'text' && preview.text === undefined && (
                  <p>
                    This file is larger than the 2 MiB text preview limit. Download it to read the
                    full file.
                  </p>
                )}
                {preview.kind === 'download' && (
                  <p>Download this file to open it in its application.</p>
                )}
              </>
            )}
          </div>
        </DialogContent>
      </Dialog>
    </ConversationResourceProvider>
  );
}
