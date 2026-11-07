import { SessionFilePreview } from '@niuulabs/ui';
import { FolderOpen } from 'lucide-react';
import type { IFileSystemPort } from '../../ports/IFileSystemPort';
import './FilePreviewPane.css';

/** The workspace's own prefix, left out of the crumbs as Lexi's are. */
const WORKSPACE_ROOT = 'workspace';

/**
 * One workspace file in the right dock, as Lexi's file pane: its path as crumbs over the
 * preview (Markdown, code, images, PDF), opened from quick open or a path in the transcript.
 */
export function FilePreviewPane({
  sessionId,
  path,
  filesystem,
  onOpenInFiles,
}: {
  sessionId: string;
  path: string;
  filesystem: IFileSystemPort;
  /** Shows the file in the Files pane, beside its folder. */
  onOpenInFiles: () => void;
}) {
  const parts = path.split('/').filter(Boolean);
  const crumbs = parts[0] === WORKSPACE_ROOT ? parts.slice(1) : parts;
  return (
    <div className="forge-file-pane" data-testid="file-preview-pane">
      <div className="forge-file-pane__head">
        <nav className="forge-file-pane__crumbs" aria-label="File path" title={path}>
          {crumbs.map((part, index) => (
            <span key={`${index}-${part}`} aria-current={index === crumbs.length - 1}>
              {part}
            </span>
          ))}
        </nav>
        <button type="button" className="forge-file-pane__files" onClick={onOpenInFiles}>
          <FolderOpen aria-hidden="true" />
          Open in Files
        </button>
      </div>
      <div className="forge-file-pane__body">
        <SessionFilePreview sessionId={sessionId} path={path} filesystem={filesystem} />
      </div>
    </div>
  );
}
