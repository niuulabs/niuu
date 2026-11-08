import { useId, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import { Dialog, DialogContent } from '@niuulabs/ui';
import type { ForgeProject, ForgeProjectChanges } from '../models/volundr.model';
import type { IVolundrService } from '../ports/IVolundrService';
import { errorText } from './errorText';
import './ProjectSettings.css';

type Busy = 'save' | 'connect' | 'archive';

export interface ProjectSettingsProps {
  project: ForgeProject;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The project was archived; the caller leaves the project's home. */
  onArchived: () => void;
}

function ProjectSettingsForm({
  project,
  onDone,
  onArchived,
}: {
  project: ForgeProject;
  onDone: () => void;
  onArchived: () => void;
}) {
  const volundr = useService<IVolundrService>('volundr');
  const queryClient = useQueryClient();
  const ids = useId();
  // The project as Forge last answered; every change is made against its revision.
  const [current, setCurrent] = useState(project);
  const [name, setName] = useState(project.name);
  const [description, setDescription] = useState(project.description ?? '');
  const [brief, setBrief] = useState(project.brief ?? '');
  const [folder, setFolder] = useState(project.workspace_path ?? '');
  const [busy, setBusy] = useState<Busy | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmArchive, setConfirmArchive] = useState(false);

  const changes: ForgeProjectChanges = {};
  if (name.trim() !== current.name) changes.name = name.trim();
  if (description !== (current.description ?? '')) changes.description = description;
  if (brief !== (current.brief ?? '')) changes.brief = brief;
  const dirty = Object.keys(changes).length > 0;
  const canSave = dirty && name.trim() !== '' && busy === null;
  const trimmedFolder = folder.trim();
  const canConnect =
    trimmedFolder !== '' && trimmedFolder !== (current.workspace_path ?? '') && busy === null;
  // A checkout lives on the host that owns the project; Forge cannot move it elsewhere.
  const hostName = current.instance_name ?? current.instance_id ?? 'This host';

  async function apply(kind: Busy, requested: ForgeProjectChanges): Promise<ForgeProject | null> {
    setBusy(kind);
    setError(null);
    try {
      const updated = await volundr.updateProject(current, requested);
      setCurrent(updated);
      await queryClient.invalidateQueries({ queryKey: ['volundr', 'projects'] });
      return updated;
    } catch (cause) {
      setError(errorText(cause));
      return null;
    } finally {
      setBusy(null);
    }
  }

  async function save() {
    const updated = await apply('save', changes);
    if (!updated) return;
    setName(updated.name);
    setDescription(updated.description ?? '');
    setBrief(updated.brief ?? '');
  }

  async function connect() {
    const updated = await apply('connect', { workspace_path: trimmedFolder });
    if (updated) setFolder(updated.workspace_path ?? trimmedFolder);
  }

  async function archive() {
    const updated = await apply('archive', { status: 'archived' });
    if (updated) onArchived();
  }

  const connected = current.workspace_path
    ? `Connected to ${current.repo_url || current.workspace_path}`
    : null;

  return (
    <form
      className="forge-project-settings"
      onSubmit={(event) => {
        event.preventDefault();
        if (canSave) void save();
      }}
      aria-label="Project settings"
    >
      <div className="forge-project-settings__body">
        <h3>Project</h3>
        <div className="forge-project-settings__group">
          <label>
            <span className="forge-project-settings__sr">Name</span>
            <input
              type="text"
              value={name}
              placeholder="Name"
              aria-label="Name"
              onChange={(event) => setName(event.target.value)}
              disabled={busy !== null}
            />
          </label>
          <label>
            <span className="forge-project-settings__sr">Description</span>
            <input
              type="text"
              value={description}
              placeholder="Description"
              aria-label="Description"
              onChange={(event) => setDescription(event.target.value)}
              disabled={busy !== null}
            />
          </label>
        </div>

        <h3>Brief</h3>
        <div className="forge-project-settings__group">
          <label>
            <span className="forge-project-settings__sr">Brief</span>
            <textarea
              rows={4}
              value={brief}
              placeholder="What this project is for, constraints, preferences…"
              aria-label="Brief"
              onChange={(event) => setBrief(event.target.value)}
              disabled={busy !== null}
            />
          </label>
        </div>
        <p className="forge-project-settings__note">
          New coordinators and workers receive the brief when they start. Running sessions keep the
          brief they started with.
        </p>

        <h3>Repository · optional</h3>
        <div className="forge-project-settings__group">
          <label className="forge-project-settings__row" htmlFor={`${ids}-host`}>
            Host
            <select
              id={`${ids}-host`}
              value={hostName}
              disabled
              title="The checkout lives on the host that owns the project"
            >
              <option value={hostName}>{hostName}</option>
            </select>
          </label>
          <label>
            <span className="forge-project-settings__sr">Git checkout folder</span>
            <input
              type="text"
              value={folder}
              placeholder="Git checkout folder on this host"
              aria-label="Git checkout folder"
              onChange={(event) => setFolder(event.target.value)}
              disabled={busy !== null}
            />
          </label>
          <div className="forge-project-settings__action">
            <button type="button" disabled={!canConnect} onClick={() => void connect()}>
              {busy === 'connect' ? 'Connecting…' : 'Connect repository'}
            </button>
            {connected && (
              <span className="forge-project-settings__status" role="status">
                {connected}
              </span>
            )}
          </div>
        </div>
        <p className="forge-project-settings__note">
          A repository can keep custom agents, workflows and skills with the project. Forge reads
          the folder&apos;s Git remote.
        </p>

        <div className="forge-project-settings__group forge-project-settings__action">
          {confirmArchive ? (
            <>
              <button
                type="button"
                className="forge-project-settings__danger"
                disabled={busy !== null}
                onClick={() => void archive()}
              >
                {busy === 'archive' ? 'Archiving…' : 'Archive it'}
              </button>
              <button
                type="button"
                disabled={busy !== null}
                onClick={() => setConfirmArchive(false)}
              >
                Keep project
              </button>
            </>
          ) : (
            <button type="button" disabled={busy !== null} onClick={() => setConfirmArchive(true)}>
              Archive project
            </button>
          )}
        </div>
        <p className="forge-project-settings__note">
          Archiving hides the project. Its sessions keep running and stay in Sessions.
        </p>

        {error && (
          <p className="forge-project-settings__error" role="alert">
            {error}
          </p>
        )}
      </div>

      <footer className="forge-project-settings__footer">
        <button type="button" onClick={onDone}>
          Done
        </button>
        <button type="submit" className="forge-project-settings__save" disabled={!canSave}>
          {busy === 'save' ? 'Saving…' : 'Save'}
        </button>
      </footer>
    </form>
  );
}

/** Lexi's project settings sheet: name, description, brief, repository and archiving. */
export function ProjectSettings({ project, open, onOpenChange, onArchived }: ProjectSettingsProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title="Project settings" className="forge-project-settings-dialog">
        <ProjectSettingsForm
          project={project}
          onDone={() => onOpenChange(false)}
          onArchived={onArchived}
        />
      </DialogContent>
    </Dialog>
  );
}
