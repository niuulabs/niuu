import { useState, useEffect } from 'react';
import { ConfidenceBar, type ConfidenceLevel } from '@niuulabs/ui';
import type { ExtractedStructure, PhaseSpec } from '../ports';

function toLevel(value: number): ConfidenceLevel {
  if (value >= 80) return 'high';
  if (value >= 50) return 'medium';
  return 'low';
}

const SIZE_CLASSES: Record<'S' | 'M' | 'L', string> = {
  S: 'niuu-bg-accent-emerald/20 niuu-text-accent-emerald niuu-border-accent-emerald/30',
  M: 'niuu-bg-accent-amber/20 niuu-text-accent-amber niuu-border-accent-amber/30',
  L: 'niuu-bg-accent-orange/20 niuu-text-accent-orange niuu-border-accent-orange/30',
};

const RISK_KIND_CLASSES: Record<string, string> = {
  blast: 'niuu-bg-critical/20 niuu-text-critical niuu-border-critical/30',
  untested: 'niuu-bg-accent-amber/20 niuu-text-accent-amber niuu-border-accent-amber/30',
};

function getRiskKindClass(kind: string): string {
  return (
    RISK_KIND_CLASSES[kind] ?? 'niuu-bg-bg-elevated niuu-text-text-secondary niuu-border-border'
  );
}

interface PlanDraftProps {
  structure: ExtractedStructure;
  loading: boolean;
  error: string | null;
  onApprove(): void;
  onBack(): void;
  onReplan?(): void;
  onSaveDraft?(): void;
  onEditPhase(phaseIndex: number, name: string): void;
  onRemoveRaid?(phaseIndex: number, raidIndex: number): void;
}

interface PhaseEditorProps {
  phase: PhaseSpec;
  phaseIndex: number;
  onSave(name: string): void;
  onRemoveRaid?(raidIndex: number): void;
}

function PhaseEditor({ phase, phaseIndex, onSave, onRemoveRaid }: PhaseEditorProps) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(phase.name);

  function handleSave() {
    onSave(name);
    setEditing(false);
  }

  function handleCancel() {
    setName(phase.name);
    setEditing(false);
  }

  const avgConfidence =
    phase.raids.length > 0
      ? Math.round(phase.raids.reduce((sum, r) => sum + r.confidence, 0) / phase.raids.length)
      : 0;

  return (
    <div
      className="niuu-rounded-lg niuu-border niuu-border-border niuu-bg-bg-secondary niuu-p-4 niuu-flex niuu-flex-col niuu-gap-3"
      data-testid={`phase-${phaseIndex}`}
    >
      <div className="niuu-flex niuu-items-start niuu-justify-between niuu-gap-2">
        {editing ? (
          <div className="niuu-flex niuu-items-center niuu-gap-2 niuu-flex-1">
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              aria-label={`Edit phase ${phaseIndex + 1} name`}
              className="niuu-flex-1 niuu-rounded niuu-border niuu-border-border niuu-bg-bg-tertiary niuu-px-2 niuu-py-1 niuu-text-sm niuu-text-text-primary focus:niuu-outline-none focus:niuu-ring-2 focus:niuu-ring-brand-500/40"
              autoFocus
            />
            <button
              type="button"
              onClick={handleSave}
              className="niuu-text-xs niuu-font-medium niuu-text-brand-400 hover:niuu-text-brand-300"
            >
              Save
            </button>
            <button
              type="button"
              onClick={handleCancel}
              className="niuu-text-xs niuu-text-text-muted hover:niuu-text-text-secondary"
            >
              Cancel
            </button>
          </div>
        ) : (
          <>
            <div className="niuu-flex niuu-flex-col niuu-gap-1 niuu-flex-1 niuu-min-w-0">
              <h3 className="niuu-text-sm niuu-font-semibold niuu-text-text-primary niuu-truncate">
                {phase.name}
              </h3>
              {phase.raids.length > 0 && (
                <div className="niuu-flex niuu-items-center niuu-gap-2">
                  <ConfidenceBar level={toLevel(avgConfidence)} />
                  <span className="niuu-text-xs niuu-text-text-muted">
                    {phase.raids.length} raid{phase.raids.length !== 1 ? 's' : ''}
                  </span>
                </div>
              )}
            </div>
            <button
              type="button"
              onClick={() => setEditing(true)}
              aria-label={`Edit phase ${phaseIndex + 1}`}
              className="niuu-flex-shrink-0 niuu-text-xs niuu-font-medium niuu-text-text-muted hover:niuu-text-text-secondary niuu-border niuu-border-border niuu-rounded niuu-px-2 niuu-py-1 niuu-transition-colors"
            >
              Edit
            </button>
          </>
        )}
      </div>

      {phase.raids.length > 0 && (
        <ul className="niuu-flex niuu-flex-col niuu-gap-2 niuu-list-none niuu-p-0 niuu-m-0">
          {phase.raids.map((raid, ri) => (
            <li
              key={ri}
              className="niuu-rounded niuu-bg-bg-tertiary niuu-px-3 niuu-py-2 niuu-grid niuu-items-center niuu-gap-x-3 niuu-gap-y-0"
              style={{ gridTemplateColumns: 'auto 1fr auto auto auto' }}
            >
              {/* Col 1: Persona circle
                  TODO: Replace with <PersonaAvatar role={...} letter={...} size={22} /> once
                  RaidSpec includes a personaRole field that maps to the PersonaRole enum. */}
              <span
                className="niuu-w-6 niuu-h-6 niuu-rounded-full niuu-bg-bg-elevated niuu-flex niuu-items-center niuu-justify-center niuu-text-xs niuu-text-text-muted niuu-flex-shrink-0"
                title={raid.persona ?? 'raven'}
                aria-label={`persona: ${raid.persona ?? 'raven'}`}
              >
                {raid.persona ? raid.persona.charAt(0).toUpperCase() : 'ᚱ'}
              </span>

              {/* Col 2: Name + meta */}
              <div className="niuu-flex niuu-flex-col niuu-gap-0.5 niuu-min-w-0">
                <span className="niuu-text-xs niuu-font-medium niuu-text-text-primary niuu-truncate">
                  {raid.name}
                </span>
                <span className="niuu-text-xs niuu-text-text-muted niuu-font-mono">
                  {[
                    raid.phase,
                    raid.persona,
                    raid.estimateHours ? `est ${raid.estimateHours}h` : null,
                  ]
                    .filter(Boolean)
                    .join(' · ')}
                  {!raid.phase &&
                    !raid.persona &&
                    `~${raid.estimateHours}h · ${raid.confidence}% confidence`}
                </span>
              </div>

              {/* Col 3: Size pill (always occupies grid cell) */}
              {raid.size ? (
                <span
                  className={`niuu-text-xs niuu-font-semibold niuu-rounded niuu-border niuu-px-1.5 niuu-py-0.5 ${SIZE_CLASSES[raid.size]}`}
                >
                  {raid.size}
                </span>
              ) : (
                <span />
              )}

              {/* Col 4: Own saga (disabled stub — no backend yet) */}
              <button
                type="button"
                disabled
                aria-label={`Promote raid ${ri + 1} to own saga`}
                className="niuu-text-xs niuu-text-text-muted niuu-border niuu-border-border niuu-rounded niuu-px-1.5 niuu-py-0.5 niuu-opacity-40 niuu-cursor-not-allowed"
              >
                Own saga
              </button>

              {/* Col 5: Remove button */}
              {onRemoveRaid ? (
                <button
                  type="button"
                  onClick={() => onRemoveRaid(ri)}
                  aria-label={`Remove raid ${ri + 1}`}
                  className="niuu-text-xs niuu-text-text-muted hover:niuu-text-critical niuu-transition-colors niuu-leading-none"
                >
                  ×
                </button>
              ) : (
                <span />
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/**
 * Step 4 of the Plan wizard — review the decomposed saga structure with
 * per-phase edit buttons before approving.
 *
 * Includes: risk rows with kind badges, Re-plan button, Save as draft button,
 * and per-raid promote (Own saga) + remove buttons.
 */
export function PlanDraft({
  structure,
  loading,
  error,
  onApprove,
  onBack,
  onReplan,
  onSaveDraft,
  onEditPhase,
  onRemoveRaid,
}: PlanDraftProps) {
  const phases = structure.structure?.phases ?? [];
  const sagaName = structure.structure?.name ?? 'New Saga';
  const risks = structure.structure?.risks ?? [];

  const totalRaids = phases.reduce((sum, p) => sum + p.raids.length, 0);
  const avgConfidence =
    totalRaids > 0
      ? Math.round(
          phases.flatMap((p) => p.raids).reduce((sum, r) => sum + r.confidence, 0) / totalRaids,
        )
      : 0;

  const [draftSavedFeedback, setDraftSavedFeedback] = useState(false);

  useEffect(() => {
    if (!draftSavedFeedback) return;
    const id = setTimeout(() => setDraftSavedFeedback(false), 2000);
    return () => clearTimeout(id);
  }, [draftSavedFeedback]);

  function handleSaveDraft() {
    onSaveDraft?.();
    setDraftSavedFeedback(true);
  }

  return (
    <div className="niuu-flex niuu-flex-col niuu-gap-4">
      <div className="tyr-plan-card niuu-flex niuu-flex-col niuu-gap-4">
        <div className="tyr-plan-step-head">
          <div className="tyr-plan-step-index">4</div>
          <div className="niuu-flex niuu-flex-col niuu-gap-1">
            <h2 className="niuu-text-lg niuu-font-semibold niuu-text-text-primary">
              Review your plan
            </h2>
            <p className="niuu-text-sm niuu-text-text-secondary">
              Nothing is created yet. Tune the draft, remove work, or re-plan before materializing
              the saga.
            </p>
          </div>
        </div>

        <div className="tyr-plan-draft-head">
          <div className="niuu-flex niuu-flex-col niuu-gap-1 niuu-min-w-0">
            <span className="niuu-text-[10px] niuu-font-mono niuu-uppercase niuu-tracking-[0.22em] niuu-text-text-muted">
              Proposed title
            </span>
            <span className="niuu-text-base niuu-font-semibold niuu-text-text-primary niuu-truncate">
              {sagaName}
            </span>
          </div>
          <div className="niuu-flex niuu-flex-wrap niuu-gap-2 niuu-justify-end niuu-text-[10px] niuu-font-mono niuu-text-text-muted">
            <span>{phases.length} phases</span>
            <span>·</span>
            <span>{totalRaids} raids</span>
            <span>·</span>
            <span>{avgConfidence}% confidence</span>
          </div>
        </div>
      </div>

      {risks.length > 0 && (
        <div className="tyr-plan-card niuu-flex niuu-flex-col niuu-gap-2">
          <p className="niuu-text-xs niuu-font-semibold niuu-text-text-muted niuu-uppercase niuu-tracking-wider niuu-font-mono">
            Risks flagged by planning raid
          </p>
          <ul className="niuu-flex niuu-flex-col niuu-gap-2 niuu-list-none niuu-p-0 niuu-m-0">
            {risks.map((risk, i) => (
              <li key={i} className="niuu-flex niuu-items-start niuu-gap-2 niuu-text-sm">
                <span
                  className={`niuu-flex-shrink-0 niuu-text-xs niuu-font-semibold niuu-rounded niuu-border niuu-px-1.5 niuu-py-0.5 niuu-uppercase niuu-font-mono ${getRiskKindClass(risk.kind)}`}
                >
                  {risk.kind}
                </span>
                <span className="niuu-text-xs niuu-text-text-secondary niuu-leading-relaxed">
                  {risk.message}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {phases.length === 0 && (
        <p className="niuu-text-sm niuu-text-text-muted niuu-italic">
          No phases extracted — the raven couldn&apos;t decompose this goal. Try going back and
          adding more context.
        </p>
      )}

      <div className="niuu-flex niuu-flex-col niuu-gap-3">
        {phases.map((phase, idx) => (
          <PhaseEditor
            key={idx}
            phase={phase}
            phaseIndex={idx}
            onSave={(name) => onEditPhase(idx, name)}
            onRemoveRaid={onRemoveRaid ? (ri) => onRemoveRaid(idx, ri) : undefined}
          />
        ))}
      </div>

      {error && (
        <p role="alert" className="niuu-text-sm niuu-text-critical">
          {error}
        </p>
      )}

      <div className="niuu-flex niuu-items-center niuu-gap-2">
        <button
          type="button"
          onClick={onBack}
          disabled={loading}
          className="tyr-plan-secondary-btn disabled:niuu-opacity-40"
        >
          ← Back
        </button>
        {onReplan && (
          <button
            type="button"
            onClick={onReplan}
            disabled={loading}
            className="tyr-plan-secondary-btn disabled:niuu-opacity-40"
          >
            ↻ Re-plan
          </button>
        )}
        <span className="niuu-flex-1" />
        {onSaveDraft && (
          <div className="niuu-flex niuu-items-center niuu-gap-2">
            {draftSavedFeedback && (
              <span
                className="niuu-text-xs niuu-text-text-muted niuu-font-mono"
                role="status"
                aria-live="polite"
              >
                Draft saved (local only)
              </span>
            )}
            <button
              type="button"
              onClick={handleSaveDraft}
              disabled={loading}
              className="tyr-plan-secondary-btn disabled:niuu-opacity-40"
            >
              Save as draft
            </button>
          </div>
        )}
        <button
          type="button"
          onClick={onApprove}
          disabled={loading || phases.length === 0}
          className="tyr-plan-primary-btn disabled:niuu-opacity-50 disabled:niuu-cursor-not-allowed"
        >
          {loading ? 'Launching…' : 'Approve & launch →'}
        </button>
      </div>
    </div>
  );
}
