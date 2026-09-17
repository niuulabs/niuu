import { useState } from 'react';
import { ChevronDown, Copy, FolderOpen, GitBranch } from 'lucide-react';
import { ConversationLink, Popover, PopoverContent, PopoverTrigger } from '@niuulabs/ui';
import type { VolundrSession } from '../models/volundr.model';
import './SessionEnvironment.css';

export function SessionEnvironment({
  session,
  repoUrl,
  repoLabel,
}: {
  session: VolundrSession;
  repoUrl?: string | null;
  repoLabel?: string;
}) {
  const [feedback, setFeedback] = useState('');
  const local = session.source.type === 'local_mount' ? session.source : null;
  const git = session.source.type === 'git' ? session.source : null;
  const folder = local
    ? (local.local_path ?? local.path ?? local.paths?.[0]?.host_path)
    : '/workspace';
  async function copy(text: string, label: string) {
    try {
      await navigator.clipboard.writeText(text);
      setFeedback(`${label} copied`);
    } catch {
      setFeedback('Could not copy. Select and copy the value.');
    }
  }
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          className="forge-environment-trigger"
          aria-label="Session workspace details"
          title={folder ?? git?.repo}
        >
          {local ? <FolderOpen size={15} /> : <GitBranch size={15} />}
          <span>{local ? 'Local mount' : repoLabel}</span>
          <ChevronDown size={13} />
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className="forge-environment-popover">
        <div className="forge-environment-heading">
          <FolderOpen size={17} />
          <strong>Session workspace</strong>
        </div>
        <dl>
          <dt>Folder</dt>
          <dd>
            {folder ? (
              <>
                <span>{folder}</span>
                <button
                  type="button"
                  aria-label="Copy folder path"
                  onClick={() => void copy(folder, 'Folder path')}
                >
                  <Copy size={15} />
                </button>
              </>
            ) : (
              <span>Not reported by Forge</span>
            )}
          </dd>
          <dt>Git repository</dt>
          <dd>
            {git ? (
              repoUrl ? (
                <ConversationLink href={repoUrl}>{git.repo}</ConversationLink>
              ) : (
                <span>{git.repo}</span>
              )
            ) : (
              <span className="forge-environment-muted">Not reported for this local mount</span>
            )}
          </dd>
          {git?.branch && (
            <>
              <dt>Branch</dt>
              <dd>
                <span>{git.branch}</span>
                <button
                  type="button"
                  aria-label="Copy branch"
                  onClick={() => void copy(git.branch, 'Branch')}
                >
                  <Copy size={15} />
                </button>
              </dd>
            </>
          )}
        </dl>
        <footer>
          <span>Session ID</span>
          <div>
            <code>{session.id}</code>
            <button
              type="button"
              aria-label="Copy session ID"
              onClick={() => void copy(session.id, 'Session ID')}
            >
              <Copy size={15} />
            </button>
          </div>
        </footer>
        <p role="status">{feedback}</p>
      </PopoverContent>
    </Popover>
  );
}
