import { useState } from 'react';
import { useService } from '@niuulabs/plugin-sdk';
import { Terminal } from './Terminal/Terminal';
import { FileTree } from './FileTree/FileTree';
import { FileViewer } from './FileTree/FileViewer';
import type { IPtyStream } from '../ports/IPtyStream';
import type { IFileSystemPort, FileTreeNode } from '../ports/IFileSystemPort';
import { useQuery } from '@tanstack/react-query';

interface VolundrSessionPageProps {
  sessionId: string;
  readOnly?: boolean;
}

/** Session detail page combining Terminal and FileTree surfaces. */
export function VolundrSessionPage({ sessionId, readOnly = false }: VolundrSessionPageProps) {
  const ptyStream = useService<IPtyStream>('ptyStream');
  const filesystem = useService<IFileSystemPort>('filesystem');

  const [activePath, setActivePath] = useState<string | undefined>(undefined);
  const [fileContent, setFileContent] = useState<string>('');
  const [fileLoading, setFileLoading] = useState(false);
  const [fileError, setFileError] = useState<string | undefined>(undefined);

  const tree = useQuery<FileTreeNode[]>({
    queryKey: ['volundr', 'filetree', sessionId],
    queryFn: () => filesystem.listTree(sessionId),
  });

  async function handleOpenFile(path: string) {
    setActivePath(path);
    setFileLoading(true);
    setFileError(undefined);
    setFileContent('');
    try {
      const content = await filesystem.readFile(sessionId, path);
      setFileContent(content);
    } catch (err) {
      setFileError(err instanceof Error ? err.message : 'Failed to load file');
    } finally {
      setFileLoading(false);
    }
  }

  return (
    <div
      className="niuu-flex niuu-h-full niuu-flex-col niuu-gap-4 niuu-p-4"
      data-testid="volundr-session-page"
    >
      <div className="niuu-flex niuu-items-center niuu-gap-2">
        <span className="niuu-font-mono niuu-text-sm niuu-text-text-secondary">session:</span>
        <span
          className="niuu-rounded niuu-bg-bg-elevated niuu-px-2 niuu-py-0.5 niuu-font-mono niuu-text-sm niuu-text-text-primary"
          data-testid="session-id-label"
        >
          {sessionId}
        </span>
        {readOnly && (
          <span className="niuu-rounded niuu-bg-bg-elevated niuu-px-2 niuu-py-0.5 niuu-text-xs niuu-text-text-muted">
            archived
          </span>
        )}
      </div>

      <div className="niuu-grid niuu-min-h-0 niuu-flex-1 niuu-grid-cols-[220px_1fr_1fr] niuu-gap-4">
        {/* File tree panel */}
        <div className="niuu-overflow-auto niuu-rounded-md niuu-border niuu-border-border-subtle niuu-bg-bg-secondary">
          {tree.isLoading && (
            <p
              className="niuu-p-3 niuu-text-xs niuu-text-text-muted"
              data-testid="filetree-loading"
            >
              loading files…
            </p>
          )}
          {tree.isError && (
            <p className="niuu-p-3 niuu-text-xs niuu-text-critical" data-testid="filetree-error">
              failed to load files
            </p>
          )}
          {tree.data && (
            <FileTree nodes={tree.data} onOpenFile={handleOpenFile} activePath={activePath} />
          )}
        </div>

        {/* File viewer panel */}
        <div className="niuu-overflow-hidden">
          {activePath ? (
            <FileViewer
              path={activePath}
              content={fileContent}
              isLoading={fileLoading}
              error={fileError}
              onClose={() => setActivePath(undefined)}
            />
          ) : (
            <div
              className="niuu-flex niuu-h-full niuu-items-center niuu-justify-center niuu-rounded-md niuu-border niuu-border-border-subtle niuu-text-sm niuu-text-text-muted"
              data-testid="file-viewer-placeholder"
            >
              Select a file to view its contents
            </div>
          )}
        </div>

        {/* Terminal panel */}
        <div className="niuu-overflow-hidden niuu-rounded-md niuu-min-h-[300px]">
          <Terminal sessionId={sessionId} stream={ptyStream} readOnly={readOnly} />
        </div>
      </div>
    </div>
  );
}
