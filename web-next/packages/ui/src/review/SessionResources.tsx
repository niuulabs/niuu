import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import {
  ConversationResourceProvider,
  ExternalLinkPreview,
  ToolImagePreview,
  externalResource,
  type ConversationResource,
} from '../chat';
import { Dialog, DialogContent } from '../primitives/Dialog';
import type { IFileSystemPort } from './IFileSystemPort';
import { presentedResource, resolveSessionResource, type PresentedFileRef } from './resourcePaths';
import './SessionResources.css';
import { ResourcePreviewContent, loadPreview, type LoadedPreview } from './ResourcePreviewContent';

const NO_PRESENTED: readonly PresentedFileRef[] = [];

export const STOPPED_SESSION_FILES =
  'This session has stopped, so the files in its workspace and home are gone.';

export function SessionResources({
  sessionId,
  workspace,
  filesystem,
  presented = NO_PRESENTED,
  filesReadable = true,
  onOpenWorkspaceFile,
  onOpenExternalLink,
  children,
}: {
  sessionId: string;
  workspace?: string;
  filesystem: IFileSystemPort;
  /** Files the session presented; their links open Forge's durable copy. */
  presented?: readonly PresentedFileRef[];
  /** False once the session has stopped: its own files can no longer be read. */
  filesReadable?: boolean;
  /**
   * Opens a workspace file the conversation links to somewhere of the host's own, such as a
   * dock pane beside the chat, instead of this dialog. Used only while the files are readable.
   */
  onOpenWorkspaceFile?: (path: string) => void;
  /**
   * Offered every external link the conversation opens first. Returns true when the host
   * opened it itself (a session's `localhost:<port>` in its live preview, say), so the link
   * preview dialog stays closed.
   */
  onOpenExternalLink?: (href: string) => boolean;
  children: ReactNode;
}) {
  const returnFocus = useRef<HTMLElement | null>(null);
  const isOpen = useRef(false);
  const [selected, setSelected] = useState<ConversationResource | null>(null);
  const [preview, setPreview] = useState<LoadedPreview>();
  const [error, setError] = useState<string>();
  const load = useCallback(
    (resource: ConversationResource, signal: AbortSignal) => {
      if (resource.kind === 'tool-image') return resource.loadFull(signal);
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
      if (!filesReadable) return Promise.reject(new Error(STOPPED_SESSION_FILES));
      if (!filesystem.downloadFile)
        return Promise.reject(new Error('File downloads are unavailable on this backend.'));
      return filesystem.downloadFile(sessionId, resource.path, signal);
    },
    [filesystem, sessionId, filesReadable],
  );
  const open = useCallback(
    (resource: ConversationResource) => {
      if (resource.kind === 'workspace' && filesReadable && onOpenWorkspaceFile) {
        onOpenWorkspaceFile(resource.path);
        return;
      }
      if (resource.kind === 'external' && onOpenExternalLink?.(resource.path)) return;
      if (!isOpen.current) {
        returnFocus.current =
          document.activeElement instanceof HTMLElement ? document.activeElement : null;
        isOpen.current = true;
      }
      setPreview(undefined);
      setError(undefined);
      setSelected(resource);
    },
    [filesReadable, onOpenWorkspaceFile, onOpenExternalLink],
  );
  const port = useMemo(
    () => ({
      resolve: (href: string) => {
        const inSession = resolveSessionResource(href, workspace);
        return presentedResource(href, inSession, presented) ?? inSession ?? externalResource(href);
      },
      load,
      open,
    }),
    [workspace, presented, load, open],
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
    if (!selected || selected.kind === 'external' || selected.kind === 'tool-image') return;
    const abort = new AbortController();
    let url: string | undefined;
    void load(selected, abort.signal)
      .then(async (blob) => {
        const loaded = await loadPreview(blob, selected.name, selected.mime);
        if (abort.signal.aborted) {
          URL.revokeObjectURL(loaded.url);
          return;
        }
        url = loaded.url;
        setPreview(loaded);
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
            selected?.kind === 'tool-image'
              ? selected.description
              : selected?.kind !== 'presented'
                ? selected?.path
                : 'Delivered by this session'
          }
        >
          {error && (
            <div className="forge-resource-empty">
              <p role="alert">{error}</p>
              {error !== STOPPED_SESSION_FILES ? (
                <button type="button" onClick={() => selected && open({ ...selected })}>
                  Try again
                </button>
              ) : presented.length > 0 ? (
                <div className="forge-resource-presented" aria-label="Files it presented">
                  <p>Forge kept the files it presented:</p>
                  <ul>
                    {presented.map((file) => (
                      <li key={file.fileId}>
                        <button
                          type="button"
                          onClick={() =>
                            open({
                              kind: 'presented',
                              path: file.fileId,
                              name: file.name,
                              ...(file.mime ? { mime: file.mime } : {}),
                            })
                          }
                        >
                          {file.name}
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </div>
          )}
          {selected?.kind !== 'external' &&
            selected?.kind !== 'tool-image' &&
            !preview &&
            !error && (
              <div className="forge-resource-empty">
                <p role="status">Loading file…</p>
              </div>
            )}
          {selected?.kind === 'tool-image' && (
            <ToolImagePreview key={selected.path} resource={selected} />
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
