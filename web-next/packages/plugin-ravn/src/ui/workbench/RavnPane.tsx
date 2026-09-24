import { useState, type KeyboardEvent } from 'react';
import { ChevronLeft, MessageSquare, Pause, Play, RotateCw, Trash2 } from 'lucide-react';
import { Dialog, DialogContent, relTime } from '@niuulabs/ui';
import type { Ravn } from '../../domain/ravn';
import {
  canRestartResident,
  canResumeResident,
  canSuspendResident,
  nameForRavn,
} from '../../domain/residentActions';
import { ravnLifeState, ravnTarget } from '../../application/ravnWorkbench';
import { useDeleteResident, useResidentLifecycle } from '../hooks/useResidentControl';
import type { ResidentLifecycleAction } from '../../ports';
import { EngineLabel, RavnMark, RavnStateBadge } from './RavnMark';
import { HealthBanner } from './HealthBanner';
import { ChatTab } from './ChatTab';
import { ActivityTab } from './ActivityTab';
import { SetupTab } from './SetupTab';
import { UsageTab } from './UsageTab';
import { errorText } from './errorText';

export type RavnTab = 'chat' | 'activity' | 'setup' | 'usage';

export const RAVN_TABS: Array<{ id: RavnTab; label: string }> = [
  { id: 'chat', label: 'Chat' },
  { id: 'activity', label: 'Activity' },
  { id: 'setup', label: 'Setup' },
  { id: 'usage', label: 'Usage' },
];

export function isRavnTab(value: unknown): value is RavnTab {
  return RAVN_TABS.some((tab) => tab.id === value);
}

export interface RavnPaneProps {
  ravn: Ravn;
  tab: RavnTab;
  onTabChange: (tab: RavnTab) => void;
  sessionId: string | null;
  onSessionChange: (sessionId: string | null) => void;
  onOpenPersona: (name: string) => void;
  onBack: () => void;
  onDeleted: () => void;
}

function DeleteDialog({
  ravn,
  open,
  onOpenChange,
  onDeleted,
}: {
  ravn: Ravn;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onDeleted: () => void;
}) {
  const remove = useDeleteResident();
  async function confirm() {
    try {
      await remove.mutateAsync(ravn);
    } catch {
      return;
    }
    onOpenChange(false);
    onDeleted();
  }
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) remove.reset();
        onOpenChange(next);
      }}
    >
      <DialogContent
        title={`Delete ${nameForRavn(ravn)}`}
        description={`This removes the ${ravn.engine ?? ''} runtime from ${ravnTarget(ravn)} and everything its ${ravn.backend ?? 'deployment'} backend created for it. Conversations kept by the engine go with it.`}
      >
        {remove.isError && (
          <div className="rw-form-error" role="alert">
            {errorText(remove.error, 'Deleting the ravn failed')}
          </div>
        )}
        <div className="rw-dialog-foot">
          <span className="rw-dialog-foot__sum" />
          <button type="button" className="rw-btn" onClick={() => onOpenChange(false)}>
            Cancel
          </button>
          <button
            type="button"
            className="rw-btn rw-btn--danger"
            onClick={() => void confirm()}
            disabled={remove.isPending}
            data-testid="ravn-delete-confirm"
          >
            {remove.isPending ? 'Deleting…' : 'Delete'}
          </button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

export function RavnPane({
  ravn,
  tab,
  onTabChange,
  sessionId,
  onSessionChange,
  onOpenPersona,
  onBack,
  onDeleted,
}: RavnPaneProps) {
  const [deleteOpen, setDeleteOpen] = useState(false);
  const lifecycle = useResidentLifecycle();
  const state = ravnLifeState(ravn);
  const name = nameForRavn(ravn);

  function apply(action: ResidentLifecycleAction) {
    lifecycle.mutate({ ravn, action });
  }

  function onTabKey(event: KeyboardEvent<HTMLButtonElement>, current: RavnTab) {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
    event.preventDefault();
    const index = RAVN_TABS.findIndex((candidate) => candidate.id === current);
    const offset = event.key === 'ArrowRight' ? 1 : -1;
    const next = RAVN_TABS[(index + offset + RAVN_TABS.length) % RAVN_TABS.length]!;
    onTabChange(next.id);
    document.getElementById(`ravn-tab-${next.id}`)?.focus();
  }

  return (
    <section className="rw-detail" aria-label={name} data-testid="ravn-pane">
      <header className="rw-head">
        <button type="button" className="rw-btn rw-back" onClick={onBack}>
          <ChevronLeft size={14} aria-hidden="true" />
          Ravens
        </button>
        <RavnMark ravn={ravn} size="lg" />
        <div className="rw-head__who">
          <h1 className="rw-head__title">
            {name} <RavnStateBadge state={state} />
          </h1>
          <div className="rw-chips">
            {ravn.personaName ? (
              <button
                type="button"
                className="rw-chip"
                onClick={() => onOpenPersona(ravn.personaName)}
                title="Open persona"
              >
                <span className="rw-chip__k">persona</span>
                {ravn.personaName}
              </button>
            ) : (
              <span className="rw-chip">
                <span className="rw-chip__k">persona</span>engine default
              </span>
            )}
            <span className="rw-chip">
              <EngineLabel engine={ravn.engine} />
              {ravn.backend && (
                <>
                  <span className="rw-chip__k">on</span>
                  {ravn.backend}
                </>
              )}
            </span>
            <span className="rw-chip rw-chip--mono">{ravn.model}</span>
            <span className="rw-chip">
              <span className="rw-chip__k">target</span>
              {ravnTarget(ravn)}
            </span>
            {ravn.flockRole && (
              <span className="rw-chip">
                <span className="rw-chip__k">flock</span>
                {ravn.flockRole}
              </span>
            )}
            <span className="rw-chip">
              <span className="rw-chip__k">deployed</span>
              {relTime(ravn.createdAt)}
            </span>
          </div>
        </div>
        <div className="rw-actions">
          <button
            type="button"
            className="rw-btn rw-btn--primary"
            onClick={() => onTabChange('chat')}
            disabled={state !== 'running'}
            title={state === 'running' ? `Talk with ${name}` : 'Only a running ravn can talk'}
            data-testid="ravn-talk"
          >
            <MessageSquare size={14} aria-hidden="true" />
            Talk
          </button>
          {canResumeResident(ravn) && (
            <button
              type="button"
              className="rw-icon-btn"
              onClick={() => apply('resume')}
              disabled={lifecycle.isPending}
              aria-label="Resume"
              title="Resume"
              data-testid="ravn-resume"
            >
              <Play size={15} aria-hidden="true" />
            </button>
          )}
          {canSuspendResident(ravn) && (
            <button
              type="button"
              className="rw-icon-btn"
              onClick={() => apply('suspend')}
              disabled={lifecycle.isPending}
              aria-label="Suspend"
              title="Suspend"
              data-testid="ravn-suspend"
            >
              <Pause size={15} aria-hidden="true" />
            </button>
          )}
          {ravn.managed && (
            <button
              type="button"
              className="rw-icon-btn"
              onClick={() => apply('restart')}
              disabled={lifecycle.isPending || !canRestartResident(ravn)}
              aria-label="Restart"
              title={
                canRestartResident(ravn)
                  ? 'Restart'
                  : 'Restart is available once it is running or failed'
              }
              data-testid="ravn-restart"
            >
              <RotateCw size={15} aria-hidden="true" />
            </button>
          )}
          {ravn.managed && ravn.observedState !== 'deleting' && (
            <button
              type="button"
              className="rw-icon-btn rw-icon-btn--danger"
              onClick={() => setDeleteOpen(true)}
              aria-label="Delete"
              title="Delete"
              data-testid="ravn-delete"
            >
              <Trash2 size={15} aria-hidden="true" />
            </button>
          )}
        </div>
      </header>

      {lifecycle.isError && (
        <p className="rw-inline-error" role="alert">
          {errorText(lifecycle.error, 'The command failed')}
        </p>
      )}

      <HealthBanner
        ravn={ravn}
        onLifecycle={apply}
        onShowActivity={() => onTabChange('activity')}
        lifecyclePending={lifecycle.isPending}
      />

      <nav className="rw-tabs" role="tablist" aria-label="Ravn sections">
        {RAVN_TABS.map((option) => (
          <button
            key={option.id}
            type="button"
            role="tab"
            id={`ravn-tab-${option.id}`}
            aria-selected={tab === option.id}
            aria-controls="ravn-tab-panel"
            tabIndex={tab === option.id ? 0 : -1}
            className="rw-tab"
            onClick={() => onTabChange(option.id)}
            onKeyDown={(event) => onTabKey(event, option.id)}
            data-testid={`ravn-tab-${option.id}`}
          >
            {option.label}
          </button>
        ))}
      </nav>

      <div
        id="ravn-tab-panel"
        role="tabpanel"
        aria-labelledby={`ravn-tab-${tab}`}
        className={tab === 'chat' ? 'rw-body rw-body--flush' : 'rw-body'}
      >
        {tab === 'chat' && (
          <ChatTab
            ravn={ravn}
            requestedSessionId={sessionId}
            onSelectSession={onSessionChange}
            onLifecycle={apply}
            onShowActivity={() => onTabChange('activity')}
            lifecyclePending={lifecycle.isPending}
          />
        )}
        {tab === 'activity' && <ActivityTab ravn={ravn} />}
        {tab === 'setup' && <SetupTab ravn={ravn} onOpenPersona={onOpenPersona} />}
        {tab === 'usage' && <UsageTab ravn={ravn} />}
      </div>

      <DeleteDialog
        ravn={ravn}
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        onDeleted={onDeleted}
      />
    </section>
  );
}
