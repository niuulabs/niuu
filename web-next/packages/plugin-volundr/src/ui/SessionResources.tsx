import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import {
  ConversationResourceProvider,
  Dialog,
  DialogContent,
  ExternalLinkPreview,
  externalResource,
  type ConversationResource,
} from '@niuulabs/ui';
import type { IFileSystemPort } from '../ports/IFileSystemPort';
import { resolveSessionResource } from '../domain/sessionResources';
import './SessionResources.css';
import { classifyPreview, isTextPreview } from '../domain/filePreview';
import { ResourcePreviewContent, type LoadedPreview } from './ResourcePreviewContent';

const INLINE_TEXT_LIMIT = 2 * 1024 * 1024;

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
  const returnFocus = useRef<HTMLElement | null>(null);
  const isOpen = useRef(false);
  const [selected, setSelected] = useState<ConversationResource | null>(null);
  const [preview, setPreview] = useState<LoadedPreview>();
  const [error, setError] = useState<string>();
  const load = useCallback(
    (resource: ConversationResource, signal: AbortSignal) => {
      if (resource.kind === 'external') {
        return fetch(resource.path, { signal, credentials: 'omit' }).then((response) => {
          if (!response.ok) throw new Error('Could not load linked file');
          return response.blob();
        });
      }
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
    if (!isOpen.current) {
      returnFocus.current =
        document.activeElement instanceof HTMLElement ? document.activeElement : null;
      isOpen.current = true;
    }
    setPreview(undefined);
    setError(undefined);
    setSelected(resource);
  }, []);
  const port = useMemo(
    () => ({
      resolve: (href: string) => resolveSessionResource(href, workspace) ?? externalResource(href),
      load,
      open,
    }),
    [workspace, load, open],
  );

  const previewPort = useMemo(
    () => ({
      ...port,
      resolve: (href: string) => {
        if (selected?.kind === 'external' && !href.startsWith('#')) {
          try {
            return externalResource(new URL(href, selected.path).href);
          } catch {
            return null;
          }
        }
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
    if (!selected || selected.kind === 'external') return;
    const abort = new AbortController();
    let url: string | undefined;
    void load(selected, abort.signal)
      .then(async (blob) => {
        const mime = selected.mime || blob.type;
        const { kind, language } = classifyPreview(selected.name, mime);
        const text =
          isTextPreview(kind) && blob.size <= INLINE_TEXT_LIMIT ? await blob.text() : undefined;
        if (abort.signal.aborted) return;
        url = URL.createObjectURL(
          kind === 'pdf' && blob.type !== 'application/pdf'
            ? new Blob([blob], { type: 'application/pdf' })
            : blob,
        );
        setPreview({ url, blob, text, kind, language });
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
          className="forge-resource-dialog"
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            isOpen.current = false;
            returnFocus.current?.focus();
          }}
          title={selected?.name ?? 'File preview'}
          description={
            selected?.kind !== 'presented' ? selected?.path : 'Delivered by this session'
          }
        >
          {error && (
            <div className="forge-resource-empty">
              <p role="alert">{error}</p>
              <button type="button" onClick={() => selected && open({ ...selected })}>
                Try again
              </button>
            </div>
          )}
          {selected?.kind !== 'external' && !preview && !error && (
            <div className="forge-resource-empty">
              <p role="status">Loading file…</p>
            </div>
          )}
          {selected?.kind === 'external' && (
            <ConversationResourceProvider port={previewPort}>
              <ExternalLinkPreview key={selected.path} href={selected.path} name={selected.name} />
            </ConversationResourceProvider>
          )}
          {preview && selected && (
            <ConversationResourceProvider port={previewPort}>
              <ResourcePreviewContent
                key={`${selected.kind}:${selected.path}`}
                resource={selected}
                preview={preview}
              />
            </ConversationResourceProvider>
          )}
        </DialogContent>
      </Dialog>
    </ConversationResourceProvider>
  );
}
