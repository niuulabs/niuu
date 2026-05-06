import { useState } from 'react';
import { useNavigate, useParams } from '@tanstack/react-router';
import { LoadingState, ErrorState, EmptyState, PersonaAvatar } from '@niuulabs/ui';
import type { PersonaRole } from '@niuulabs/domain';
import type { RaidStatus, Saga, Phase, Raid } from '../domain/saga';
import { useAssignSagaWorkflow, useSaga } from './useSaga';
import { usePhases } from './usePhases';
import { WorkflowCard } from './WorkflowCard';
import { SagaWorkflowModal } from './SagaWorkflowModal';
import { StageProgressRail } from './StageProgressRail';
import { ConfidenceDriftCard } from './ConfidenceDriftCard';

function statusLabel(status: RaidStatus | Saga['status'] | Phase['status']): string {
  switch (status) {
    case 'active':
      return 'ACTIVE';
    case 'complete':
      return 'COMPLETE';
    case 'failed':
      return 'FAILED';
    case 'pending':
      return 'PENDING';
    case 'queued':
      return 'QUEUED';
    case 'running':
      return 'RUNNING';
    case 'review':
      return 'REVIEW';
    case 'escalated':
      return 'ESCALATED';
    case 'merged':
      return 'MERGED';
    case 'gated':
      return 'GATED';
  }
}

function statusClasses(status: RaidStatus | Saga['status'] | Phase['status']): string {
  const base =
    'niuu-inline-flex niuu-items-center niuu-gap-2 niuu-min-w-[116px] niuu-justify-center niuu-rounded-full niuu-border niuu-px-3 niuu-py-1 niuu-text-[11px] niuu-font-mono niuu-tracking-[0.1em]';
  if (status === 'failed')
    return `${base} niuu-border-critical/50 niuu-text-critical-fg niuu-bg-critical-bg`;
  if (status === 'complete' || status === 'merged')
    return `${base} niuu-border-border niuu-text-text-primary niuu-bg-bg-tertiary`;
  if (status === 'active' || status === 'running' || status === 'review')
    return `${base} niuu-border-brand/45 niuu-text-brand-200 niuu-bg-brand/10`;
  if (status === 'escalated' || status === 'gated')
    return `${base} niuu-border-accent-amber/45 niuu-text-accent-amber niuu-bg-accent-amber/10`;
  return `${base} niuu-border-border niuu-text-text-muted niuu-bg-bg-tertiary`;
}

function confidenceTone(value: number): string {
  if (value >= 85) return 'niuu-bg-brand';
  if (value >= 65) return 'niuu-bg-brand/80';
  if (value >= 45) return 'niuu-bg-accent-amber';
  return 'niuu-bg-critical';
}

function ConfidenceMeter({ value }: { value: number }) {
  const clamped = Math.max(0, Math.min(100, value));
  return (
    <div className="niuu-flex niuu-items-center niuu-gap-3 niuu-justify-end">
      <div className="niuu-w-14 niuu-h-1 niuu-rounded-full niuu-bg-bg-elevated niuu-overflow-hidden">
        <div
          className={['niuu-h-full niuu-rounded-full', confidenceTone(clamped)].join(' ')}
          style={{ width: `${clamped}%` }}
        />
      </div>
      <span className="niuu-min-w-6 niuu-text-right niuu-font-mono niuu-text-[12px] niuu-text-text-muted">
        {Math.round(clamped)}
      </span>
    </div>
  );
}

function roleForRaid(raid: Raid): PersonaRole {
  const label = `${raid.name} ${raid.trackerId}`.toLowerCase();
  if (label.includes('review')) return 'review';
  if (label.includes('qa') || label.includes('test') || label.includes('validate')) return 'verify';
  if (label.includes('ship') || label.includes('release')) return 'ship';
  if (label.includes('plan') || label.includes('decompose')) return 'plan';
  return 'build';
}

function glyphForRole(role: PersonaRole): string {
  switch (role) {
    case 'plan':
      return 'D';
    case 'build':
      return 'C';
    case 'verify':
      return 'V';
    case 'review':
      return 'R';
    case 'ship':
      return 'S';
    default:
      return '•';
  }
}

function phaseDotClasses(status: Phase['status']): string {
  const base = 'niuu-w-3 niuu-h-3 niuu-rounded-full niuu-shrink-0';
  if (status === 'complete')
    return `${base} niuu-bg-brand/90 niuu-shadow-[0_0_0_2px_rgba(125,211,252,0.14)]`;
  if (status === 'active')
    return `${base} niuu-bg-brand niuu-shadow-[0_0_0_4px_rgba(125,211,252,0.10)]`;
  if (status === 'gated') return `${base} niuu-bg-accent-amber`;
  return `${base} niuu-bg-text-muted/40`;
}

function raidDotClasses(status: RaidStatus): string {
  const base = 'niuu-w-2.5 niuu-h-2.5 niuu-rounded-full niuu-shrink-0';
  if (status === 'merged') return `${base} niuu-bg-brand/90`;
  if (status === 'running' || status === 'review') return `${base} niuu-bg-brand`;
  if (status === 'failed') return `${base} niuu-bg-critical`;
  if (status === 'escalated') return `${base} niuu-bg-accent-amber`;
  return `${base} niuu-bg-text-muted/35`;
}

function RaidPersona({ raid }: { raid: Raid }) {
  const role = roleForRaid(raid);
  return (
    <div className="niuu-flex niuu-items-center niuu-justify-center">
      <PersonaAvatar role={role} letter={glyphForRole(role)} size={22} title={raid.name} />
    </div>
  );
}

function PhaseCard({ phase }: { phase: Phase }) {
  return (
    <section className="niuu-rounded-xl niuu-border niuu-border-border-subtle niuu-bg-bg-secondary niuu-overflow-hidden">
      <div className="niuu-flex niuu-items-center niuu-justify-between niuu-gap-3 niuu-px-5 niuu-py-3.5">
        <div className="niuu-flex niuu-items-center niuu-gap-3 niuu-min-w-0">
          <span className={phaseDotClasses(phase.status)} />
          <h3 className="niuu-m-0 niuu-text-[17px] niuu-font-semibold niuu-text-text-primary">
            {`Phase ${phase.number} · ${phase.name}`}
          </h3>
        </div>
        <div className="niuu-flex niuu-items-center niuu-gap-4 niuu-shrink-0">
          <span className={statusClasses(phase.status)}>{statusLabel(phase.status)}</span>
          <ConfidenceMeter value={phase.confidence} />
        </div>
      </div>
      <div className="niuu-px-5 niuu-pb-3">
        {phase.raids.length === 0 ? (
          <div className="niuu-py-3 niuu-text-sm niuu-text-text-muted">No raids in this phase.</div>
        ) : (
          <div className="niuu-space-y-1">
            {phase.raids.map((raid) => (
              <div
                key={raid.id}
                className="niuu-grid niuu-items-center niuu-gap-4 niuu-py-3 niuu-border-t niuu-border-border-subtle"
                style={{ gridTemplateColumns: '18px 96px minmax(0,1fr) 34px 170px 78px' }}
              >
                <span className={raidDotClasses(raid.status)} />
                <span className="niuu-font-mono niuu-text-[12px] niuu-text-text-secondary">
                  {raid.trackerId}
                </span>
                <span className="niuu-text-[14px] niuu-font-medium niuu-text-text-primary niuu-truncate">
                  {raid.name}
                </span>
                <RaidPersona raid={raid} />
                <span className={statusClasses(raid.status)}>{statusLabel(raid.status)}</span>
                <ConfidenceMeter value={raid.confidence} />
              </div>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}

interface SagaDetailPageProps {
  sagaId: string;
  hideBackButton?: boolean;
}

export function SagaDetailPage({ sagaId, hideBackButton = false }: SagaDetailPageProps) {
  const navigate = useNavigate();
  const [showWorkflowModal, setShowWorkflowModal] = useState(false);
  const {
    data: saga,
    isLoading: sagaLoading,
    isError: sagaError,
    error: sagaErr,
  } = useSaga(sagaId);
  const {
    data: phases,
    isLoading: phasesLoading,
    isError: phasesError,
    error: phasesErr,
  } = usePhases(sagaId);
  const assignWorkflow = useAssignSagaWorkflow(sagaId);

  if (sagaLoading || phasesLoading) return <LoadingState label="Loading saga…" />;
  if (sagaError)
    return (
      <ErrorState message={sagaErr instanceof Error ? sagaErr.message : 'Failed to load saga'} />
    );
  if (phasesError)
    return (
      <ErrorState
        message={phasesErr instanceof Error ? phasesErr.message : 'Failed to load phases'}
      />
    );
  if (!saga) return <ErrorState message={`Saga "${sagaId}" not found`} />;

  const allPhases = phases ?? [];
  const branchLabel = `${saga.featureBranch} → ${saga.baseBranch}`;

  function handleAssignWorkflow(workflowId: string | null) {
    assignWorkflow.mutate(workflowId, {
      onSuccess: () => {
        setShowWorkflowModal(false);
      },
    });
  }

  return (
    <div className="niuu-space-y-4">
      {!hideBackButton && (
        <button
          type="button"
          onClick={() => void navigate({ to: '/tyr/sagas' })}
          className="niuu-text-sm niuu-text-text-secondary hover:niuu-text-text-primary"
        >
          ← Sagas
        </button>
      )}

      <div className="niuu-grid niuu-gap-5" style={{ gridTemplateColumns: 'minmax(0,1fr) 340px' }}>
        <div className="niuu-space-y-4">
          <div className="niuu-flex niuu-items-end niuu-justify-between niuu-gap-4 niuu-px-1">
            <div className="niuu-min-w-0">
              <div className="niuu-mb-1 niuu-text-[12px] niuu-font-mono niuu-tracking-[0.08em] niuu-text-text-muted niuu-uppercase">
                {`${saga.trackerId} · ${saga.name}`}
              </div>
              <div className="niuu-text-[13px] niuu-font-mono niuu-text-text-muted">
                {branchLabel}
              </div>
            </div>
          </div>

          {allPhases.length === 0 ? (
            <EmptyState
              title="No phases yet"
              description="This saga has not been decomposed into phases."
            />
          ) : (
            allPhases.map((phase) => <PhaseCard key={phase.id} phase={phase} />)
          )}
        </div>

        <div className="niuu-space-y-4">
          <WorkflowCard
            workflow={saga.workflow}
            workflowVersion={saga.workflowVersion}
            isUpdating={assignWorkflow.isPending}
            onAssign={() => setShowWorkflowModal(true)}
            onClear={saga.workflowId ? () => handleAssignWorkflow(null) : undefined}
          />
          <StageProgressRail phases={allPhases} />
          <ConfidenceDriftCard sagaId={saga.id} confidence={saga.confidence} />
        </div>
      </div>
      {showWorkflowModal && (
        <SagaWorkflowModal
          open={showWorkflowModal}
          onOpenChange={setShowWorkflowModal}
          sagaName={saga.name}
          onAssign={(workflow) => handleAssignWorkflow(workflow.id)}
        />
      )}
    </div>
  );
}

export function SagaDetailRoute() {
  const { sagaId } = useParams({ strict: false }) as { sagaId: string };
  return <SagaDetailPage sagaId={sagaId} />;
}
