import { useEffect, useState } from 'react';
import { cn, SegmentedFilter } from '@niuulabs/ui';
import type {
  Workflow,
  WorkflowNode,
  WorkflowResourceBinding,
  WorkflowResourceNode,
  WorkflowStageNode,
} from '../../domain/workflow';
import type { WorkflowIssue } from '../../domain/workflowValidation';
import type { WorkflowBuilderActions, WorkflowStageModelOption } from './useWorkflowBuilder';
import type { PersonaEntry } from './LibraryPanel';
import { normalizedStageMembers } from './graphUtils';
import { EPHEMERAL_LOCAL_MOUNT_ID, type WorkflowRegistryMount } from './mimirRegistry';

export interface WorkflowDetailPanelProps {
  workflow: Workflow;
  selectedNode: WorkflowNode | null;
  errorCount: number;
  warnCount: number;
  issues: WorkflowIssue[];
  personas: PersonaEntry[];
  models: WorkflowStageModelOption[];
  registryMounts: WorkflowRegistryMount[];
  onDeleteNode: WorkflowBuilderActions['deleteNode'];
  onUpdateNode: WorkflowBuilderActions['updateNode'];
  onUpdateLabel: WorkflowBuilderActions['updateNodeLabel'];
  onUpdateWorkflowMeta: WorkflowBuilderActions['updateWorkflowMeta'];
  onAddPersona: WorkflowBuilderActions['addPersonaToStage'];
  onReplacePersona: WorkflowBuilderActions['replacePersonaInStage'];
  onUpdatePersonaModel: WorkflowBuilderActions['updatePersonaModel'];
  onUpdatePersonaBudget: WorkflowBuilderActions['updatePersonaBudget'];
  onRemovePersona: WorkflowBuilderActions['removePersonaFromStage'];
  onAddResourceBinding: WorkflowBuilderActions['addResourceBinding'];
  onUpdateResourceBinding: WorkflowBuilderActions['updateResourceBinding'];
  onRemoveResourceBinding: WorkflowBuilderActions['removeResourceBinding'];
}

const SECTION_LABEL =
  'niuu-text-[9px] niuu-font-semibold niuu-uppercase niuu-tracking-[0.22em] niuu-text-text-faint niuu-font-mono';
const INPUT =
  'niuu-w-full niuu-py-2.5 niuu-px-3.5 niuu-bg-bg-tertiary niuu-border niuu-border-border-subtle niuu-rounded-lg niuu-text-text-primary niuu-font-sans niuu-text-[12px]';
const CHIP_BTN =
  'niuu-rounded-md niuu-border niuu-px-2 niuu-py-1 niuu-text-[11px] niuu-font-mono niuu-transition-colors';
const TAG =
  'niuu-inline-flex niuu-items-center niuu-rounded-md niuu-border niuu-px-2 niuu-py-0.5 niuu-text-[10px] niuu-font-mono';
const TAB_BTN =
  'niuu-px-0 niuu-py-2 niuu-bg-transparent niuu-border-none niuu-border-b-2 niuu-text-[11px] niuu-font-mono niuu-uppercase niuu-tracking-[0.18em]';
const DELETE_BTN =
  'niuu-inline-flex niuu-items-center niuu-rounded-lg niuu-border niuu-border-critical/60 niuu-bg-critical-bg/25 niuu-px-2.5 niuu-py-1.5 niuu-text-[12px] niuu-font-semibold niuu-text-[#ffb0b0]';

function issueTone(severity: WorkflowIssue['severity']) {
  return severity === 'error'
    ? 'niuu-border-critical/40 niuu-bg-critical-bg/30 niuu-text-critical'
    : 'niuu-border-status-amber/40 niuu-bg-status-amber/10 niuu-text-status-amber';
}

function personaById(personas: PersonaEntry[], id: string) {
  return personas.find((persona) => persona.id === id);
}

function personaGlyph(role?: string) {
  switch (role) {
    case 'plan':
      return 'D';
    case 'build':
      return 'C';
    case 'verify':
      return 'V';
    case 'gate':
      return 'I';
    default:
      return '•';
  }
}

function memberIssuesForPersona(issues: WorkflowIssue[], persona: PersonaEntry | undefined) {
  if (!persona) return [];
  const keys = [persona.label, ...(persona.produces ?? []), ...(persona.consumes ?? [])].map((v) =>
    v.toLowerCase(),
  );
  return issues.filter((issue) => keys.some((key) => issue.message.toLowerCase().includes(key)));
}

function triggerEventOptions(personas: PersonaEntry[], current: string): string[] {
  return [
    ...new Set(
      [
        ...personas.flatMap((persona) => persona.consumes ?? []),
        current || 'code.requested',
      ].filter(Boolean),
    ),
  ].sort();
}

function uniquePersonaIds(workflow: Workflow): string[] {
  return [
    ...new Set(
      workflow.nodes.flatMap((node) => {
        if (node.kind !== 'stage') return [];
        return normalizedStageMembers(node).map((member) => member.personaId);
      }),
    ),
  ];
}

function defaultTargetIdForType(
  workflow: Workflow,
  targetType: WorkflowResourceBinding['targetType'],
): string {
  if (targetType === 'workflow') return workflow.id;
  if (targetType === 'stage') {
    return workflow.nodes.find((node) => node.kind === 'stage')?.id ?? '';
  }
  return uniquePersonaIds(workflow)[0] ?? '';
}

function WorkflowSummary({
  workflow,
  errorCount,
  warnCount,
  onUpdateWorkflowMeta,
}: {
  workflow: Workflow;
  errorCount: number;
  warnCount: number;
  onUpdateWorkflowMeta: WorkflowBuilderActions['updateWorkflowMeta'];
}) {
  const stageCount = workflow.nodes.filter((n) => n.kind === 'stage').length;
  const triggerCount = workflow.nodes.filter((n) => n.kind === 'trigger').length;
  const gateCount = workflow.nodes.filter((n) => n.kind === 'gate').length;
  const condCount = workflow.nodes.filter((n) => n.kind === 'cond').length;
  const endCount = workflow.nodes.filter((n) => n.kind === 'end').length;

  return (
    <div className="niuu-px-4 niuu-py-3 niuu-flex niuu-flex-col niuu-gap-4">
      <div>
        <label className={SECTION_LABEL}>Name</label>
        <input
          className={INPUT}
          value={workflow.name}
          onChange={(e) => onUpdateWorkflowMeta({ name: e.target.value })}
        />
      </div>

      <div>
        <label className={SECTION_LABEL}>Description</label>
        <textarea
          className={cn(INPUT, 'niuu-min-h-[84px] niuu-leading-relaxed')}
          value={workflow.description ?? ''}
          onChange={(e) => onUpdateWorkflowMeta({ description: e.target.value })}
        />
      </div>

      <div className="niuu-grid niuu-grid-cols-2 niuu-gap-2">
        <div className="niuu-rounded-md niuu-border niuu-border-border-subtle niuu-bg-bg-elevated niuu-p-3">
          <div className={SECTION_LABEL}>Version</div>
          <input
            className={cn(INPUT, 'niuu-mt-1')}
            value={workflow.version ?? '0.1.0'}
            onChange={(e) => onUpdateWorkflowMeta({ version: e.target.value })}
          />
        </div>
        <div className="niuu-rounded-md niuu-border niuu-border-border-subtle niuu-bg-bg-elevated niuu-p-3">
          <div className={SECTION_LABEL}>Summary</div>
          <div className="niuu-mt-2 niuu-text-xs niuu-text-text-secondary niuu-leading-relaxed">
            {triggerCount} triggers · {stageCount} stages · {gateCount} gates · {condCount}{' '}
            conditions · {endCount} end nodes · {workflow.edges.length} edges
          </div>
        </div>
      </div>

      <div className="niuu-rounded-md niuu-border niuu-border-border-subtle niuu-bg-bg-elevated niuu-p-3 niuu-flex niuu-gap-2">
        <span
          className={cn(
            CHIP_BTN,
            errorCount
              ? 'niuu-border-critical niuu-text-critical'
              : 'niuu-border-border niuu-text-text-faint',
          )}
        >
          ERR {errorCount}
        </span>
        <span
          className={cn(
            CHIP_BTN,
            warnCount
              ? 'niuu-border-status-amber niuu-text-status-amber'
              : 'niuu-border-border niuu-text-text-faint',
          )}
        >
          WARN {warnCount}
        </span>
      </div>
    </div>
  );
}

function StageInspector({
  node,
  workflow,
  personas,
  models,
  issues,
  onUpdateNode,
  onUpdateLabel,
  onAddPersona,
  onReplacePersona,
  onUpdatePersonaModel,
  onUpdatePersonaBudget,
  onRemovePersona,
  onDeleteNode,
}: {
  node: WorkflowStageNode;
  workflow: Workflow;
  personas: PersonaEntry[];
  models: WorkflowStageModelOption[];
  issues: WorkflowIssue[];
  onUpdateNode: WorkflowBuilderActions['updateNode'];
  onUpdateLabel: WorkflowBuilderActions['updateNodeLabel'];
  onAddPersona: WorkflowBuilderActions['addPersonaToStage'];
  onReplacePersona: WorkflowBuilderActions['replacePersonaInStage'];
  onUpdatePersonaModel: WorkflowBuilderActions['updatePersonaModel'];
  onUpdatePersonaBudget: WorkflowBuilderActions['updatePersonaBudget'];
  onRemovePersona: WorkflowBuilderActions['removePersonaFromStage'];
  onDeleteNode: WorkflowBuilderActions['deleteNode'];
}) {
  const [tab, setTab] = useState<'config' | 'flock' | 'validate'>('config');
  const [newPersonaId, setNewPersonaId] = useState('');
  const [newModelId, setNewModelId] = useState(models[0]?.id ?? '');
  const inbound = workflow.edges.filter((edge) => edge.target === node.id);
  const outbound = workflow.edges.filter((edge) => edge.source === node.id);
  const nodeIssues = issues.filter((issue) => issue.nodeId === node.id);
  const stageMembers = normalizedStageMembers(node);
  const availableModels = models;
  const defaultModelId = availableModels[0]?.id ?? '';
  const executionOptions = [
    { value: 'parallel' as const, label: 'parallel' },
    { value: 'sequential' as const, label: 'sequential' },
  ];
  const availablePersonas = personas.filter(
    (persona) => !stageMembers.some((member) => member.personaId === persona.id),
  );

  useEffect(() => {
    if (!newModelId && defaultModelId) {
      setNewModelId(defaultModelId);
    }
  }, [defaultModelId, newModelId]);

  return (
    <div className="niuu-px-4 niuu-py-0 niuu-flex niuu-flex-col niuu-gap-4">
      <div className="niuu-flex niuu-items-center niuu-justify-between niuu-py-4 niuu-border-b niuu-border-border niuu-mx-[-16px] niuu-px-4">
        <div className="niuu-flex niuu-items-center niuu-gap-2.5">
          <span className="niuu-text-[18px] niuu-text-text-primary">◆</span>
          <span className="niuu-text-[16px] niuu-font-semibold niuu-text-text-primary">
            {node.kind === 'stage' ? 'Stage' : node.label}
          </span>
        </div>
        <span className="niuu-text-[11px] niuu-font-mono niuu-text-text-faint">{node.id}</span>
      </div>
      <div className="niuu-flex niuu-gap-5 niuu-pb-1 niuu-mb-1 niuu-border-b niuu-border-border niuu-mx-[-16px] niuu-px-4">
        {(['config', 'flock', 'validate'] as const).map((name) => (
          <button
            key={name}
            type="button"
            onClick={() => setTab(name)}
            className={cn(
              TAB_BTN,
              tab === name
                ? 'niuu-border-text-primary niuu-text-text-primary'
                : 'niuu-border-transparent niuu-text-text-faint',
            )}
          >
            {name}
          </button>
        ))}
      </div>

      {tab === 'config' && (
        <>
          <div>
            <label className={SECTION_LABEL}>Name</label>
            <input
              className={INPUT}
              value={node.label}
              onChange={(e) => onUpdateLabel(node.id, e.target.value)}
            />
          </div>

          <div>
            <label className={SECTION_LABEL}>Execution</label>
            <SegmentedFilter
              options={executionOptions}
              value={node.executionMode ?? 'parallel'}
              onChange={(mode) => onUpdateNode(node.id, { executionMode: mode })}
              aria-label="Execution mode"
              className="niuu-mt-1 niuu-rounded-xl niuu-border niuu-border-border-subtle"
            />
          </div>

          <div className="niuu-grid niuu-grid-cols-2 niuu-gap-2">
            <div>
              <label className={SECTION_LABEL}>Max concurrent</label>
              <input
                type="number"
                className={INPUT}
                value={node.maxConcurrent ?? 3}
                min={1}
                onChange={(e) =>
                  onUpdateNode(node.id, { maxConcurrent: Math.max(1, Number(e.target.value) || 1) })
                }
              />
            </div>
            <div />
          </div>

          {(inbound.length > 0 || outbound.length > 0) && (
            <div className="niuu-rounded-md niuu-border niuu-border-border-subtle niuu-bg-bg-elevated niuu-p-3 niuu-flex niuu-flex-col niuu-gap-2">
              <div className={SECTION_LABEL}>Fan-in / fan-out</div>
              <div className="niuu-text-xs niuu-text-text-secondary">
                {inbound.length} incoming · {outbound.length} outgoing
              </div>
              <SegmentedFilter
                options={[
                  { value: 'all' as const, label: 'all' },
                  { value: 'any' as const, label: 'any' },
                  { value: 'merge' as const, label: 'merge' },
                ]}
                value={node.joinMode ?? 'all'}
                onChange={(mode) => onUpdateNode(node.id, { joinMode: mode })}
                aria-label="Fan in mode"
                className="niuu-rounded-xl niuu-border niuu-border-border-subtle"
              />
            </div>
          )}

          <div className="niuu-border-t niuu-border-border niuu-pt-4">
            <button type="button" className={DELETE_BTN} onClick={() => onDeleteNode(node.id)}>
              Delete node
            </button>
          </div>
        </>
      )}

      {tab === 'flock' && (
        <>
          <div className={SECTION_LABEL}>Personas in this stage</div>
          <div className="niuu-flex niuu-flex-col niuu-gap-1.5">
            {stageMembers.length === 0 && (
              <div className="niuu-rounded-md niuu-border niuu-border-border-subtle niuu-bg-bg-elevated niuu-p-3 niuu-text-xs niuu-text-text-muted">
                No ravns assigned yet.
              </div>
            )}
            {stageMembers.map((member) => {
              const persona = personaById(personas, member.personaId);
              const memberIssues = memberIssuesForPersona(nodeIssues, persona);
              return (
                <div
                  key={member.personaId}
                  className="niuu-rounded-xl niuu-border niuu-border-border-subtle niuu-bg-bg-elevated niuu-p-3.5 niuu-flex niuu-flex-col niuu-gap-2.5"
                >
                  <div className="niuu-flex niuu-items-start niuu-gap-2">
                    <div className="niuu-flex niuu-h-8 niuu-w-8 niuu-items-center niuu-justify-center niuu-rounded-full niuu-border niuu-border-[#aeddff] niuu-text-[#d6efff] niuu-font-mono niuu-text-[18px]">
                      {personaGlyph(persona?.role)}
                    </div>
                    <div className="niuu-flex-1 niuu-min-w-0">
                      <div className="niuu-text-[13px] niuu-font-semibold niuu-text-text-primary">
                        {persona?.label ?? member.personaId}
                      </div>
                      <div className="niuu-text-[9px] niuu-font-mono niuu-text-text-faint niuu-flex niuu-flex-wrap niuu-gap-2">
                        <span>{persona?.role ?? 'unknown'}</span>
                        <span>{member.model || 'no model'}</span>
                      </div>
                    </div>
                    <button
                      type="button"
                      className="niuu-bg-transparent niuu-border-none niuu-text-text-faint niuu-text-xl niuu-leading-none"
                      onClick={() => onRemovePersona(node.id, member.personaId)}
                    >
                      ×
                    </button>
                  </div>

                  <div className="niuu-flex niuu-items-center niuu-gap-2.5">
                    <span className="niuu-text-[10px] niuu-text-text-muted">budget</span>
                    <input
                      type="number"
                      className="niuu-w-14 niuu-bg-transparent niuu-border-none niuu-p-0 niuu-text-[14px] niuu-font-semibold niuu-text-text-primary"
                      value={member.budget}
                      min={0}
                      onChange={(e) =>
                        onUpdatePersonaBudget(
                          node.id,
                          member.personaId,
                          Math.max(0, Number(e.target.value) || 0),
                        )
                      }
                    />
                  </div>

                  <div>
                    <div className={SECTION_LABEL}>Model</div>
                    <select
                      className={cn(INPUT, 'niuu-mt-0.5')}
                      value={member.model ?? ''}
                      onChange={(e) =>
                        onUpdatePersonaModel(node.id, member.personaId, e.target.value)
                      }
                    >
                      <option value="">Select a model…</option>
                      {availableModels.map((model) => (
                        <option key={model.id} value={model.id}>
                          {model.label}
                        </option>
                      ))}
                    </select>
                  </div>

                  <div className="niuu-grid niuu-grid-cols-1 niuu-gap-2">
                    <div>
                      <div className={SECTION_LABEL}>Consumes</div>
                      <div className="niuu-flex niuu-flex-wrap niuu-gap-1 niuu-mt-0.5">
                        {(persona?.consumes ?? []).map((event) => (
                          <span
                            key={event}
                            className={cn(
                              TAG,
                              'niuu-border-transparent niuu-bg-[#4f6474] niuu-text-[#d6efff]',
                            )}
                          >
                            {event}
                          </span>
                        ))}
                      </div>
                    </div>
                    <div>
                      <div className={SECTION_LABEL}>Produces</div>
                      <div className="niuu-flex niuu-flex-wrap niuu-gap-1 niuu-mt-0.5">
                        {(persona?.produces ?? []).map((event) => (
                          <span
                            key={event}
                            className={cn(
                              TAG,
                              memberIssues.length > 0
                                ? 'niuu-border-[#b75159] niuu-border-dashed niuu-bg-[#4b3136] niuu-text-[#ffb0b0]'
                                : 'niuu-border-transparent niuu-bg-bg-primary niuu-text-text-primary',
                            )}
                          >
                            {event}
                          </span>
                        ))}
                      </div>
                    </div>
                  </div>

                  {memberIssues.length > 0 && (
                    <div className="niuu-rounded-lg niuu-border niuu-border-[#b75159] niuu-bg-[#4b3136] niuu-p-3 niuu-text-[#ffb0b0]">
                      <div className="niuu-text-[12px] niuu-font-semibold niuu-leading-snug">
                        {memberIssues[0]?.message}
                      </div>
                    </div>
                  )}

                  <div>
                    <div className={SECTION_LABEL}>Ravn</div>
                    <select
                      className={cn(INPUT, 'niuu-mt-0.5')}
                      value={member.personaId}
                      onChange={(e) =>
                        onReplacePersona(node.id, member.personaId, e.target.value, member.model)
                      }
                    >
                      {personas.map((personaOption) => (
                        <option key={personaOption.id} value={personaOption.id}>
                          {personaOption.label} · {personaOption.role}
                        </option>
                      ))}
                    </select>
                  </div>
                </div>
              );
            })}
          </div>

          <div className="niuu-border-t niuu-border-border niuu-pt-6">
            <label className={SECTION_LABEL}>Add persona</label>
            <div className="niuu-grid niuu-grid-cols-1 niuu-gap-2 niuu-mt-0.5">
              <select
                className={INPUT}
                value={newPersonaId}
                onChange={(e) => setNewPersonaId(e.target.value)}
              >
                <option value="">Select a ravn…</option>
                {availablePersonas.map((persona) => (
                  <option key={persona.id} value={persona.id}>
                    {persona.label} · {persona.role}
                  </option>
                ))}
              </select>
              <select
                className={INPUT}
                value={newModelId}
                onChange={(e) => setNewModelId(e.target.value)}
              >
                <option value="">Select a model…</option>
                {availableModels.map((model) => (
                  <option key={model.id} value={model.id}>
                    {model.label}
                  </option>
                ))}
              </select>
              <button
                type="button"
                className={CHIP_BTN}
                disabled={!newPersonaId || !newModelId}
                onClick={() => {
                  if (!newPersonaId || !newModelId) return;
                  onAddPersona(node.id, newPersonaId, newModelId, 40);
                  setNewPersonaId('');
                  setNewModelId(defaultModelId);
                }}
              >
                Add ravn
              </button>
            </div>
          </div>

          <div className="niuu-border-t niuu-border-border niuu-pt-4">
            <button type="button" className={DELETE_BTN} onClick={() => onDeleteNode(node.id)}>
              Delete node
            </button>
          </div>
        </>
      )}

      {tab === 'validate' && (
        <div className="niuu-flex niuu-flex-col niuu-gap-2">
          {nodeIssues.length === 0 ? (
            <div className="niuu-rounded-md niuu-border niuu-border-status-emerald/40 niuu-bg-status-emerald/10 niuu-p-3 niuu-text-xs niuu-text-status-emerald">
              No validation issues on this node.
            </div>
          ) : (
            nodeIssues.map((issue, index) => (
              <div
                key={`${issue.kind}-${index}`}
                className={cn(
                  'niuu-rounded-xl niuu-border niuu-p-4 niuu-text-sm niuu-leading-relaxed',
                  issueTone(issue.severity),
                )}
              >
                <div className="niuu-font-semibold niuu-mb-1">{issue.kind}</div>
                <div>{issue.message}</div>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}

function ResourceInspector({
  node,
  workflow,
  registryMounts,
  onUpdateNode,
  onUpdateLabel,
  onDeleteNode,
  onAddResourceBinding,
  onUpdateResourceBinding,
  onRemoveResourceBinding,
}: {
  node: WorkflowResourceNode;
  workflow: Workflow;
  registryMounts: WorkflowRegistryMount[];
  onUpdateNode: WorkflowBuilderActions['updateNode'];
  onUpdateLabel: WorkflowBuilderActions['updateNodeLabel'];
  onDeleteNode: WorkflowBuilderActions['deleteNode'];
  onAddResourceBinding: WorkflowBuilderActions['addResourceBinding'];
  onUpdateResourceBinding: WorkflowBuilderActions['updateResourceBinding'];
  onRemoveResourceBinding: WorkflowBuilderActions['removeResourceBinding'];
}) {
  const bindings = (workflow.resourceBindings ?? []).filter(
    (binding) => binding.resourceNodeId === node.id,
  );
  const stages = workflow.nodes.filter((candidate) => candidate.kind === 'stage');
  const personaIds = uniquePersonaIds(workflow);
  const selectedMount = registryMounts.find((mount) => mount.id === node.registryEntryId) ?? null;
  const isEphemeral = node.bindingMode === 'ephemeral_local';

  function applyRegistryMount(mountId: string) {
    const mount = registryMounts.find((candidate) => candidate.id === mountId);
    if (!mount) {
      onUpdateNode(node.id, { registryEntryId: mountId || null });
      return;
    }
    onUpdateNode(node.id, {
      label: mount.name,
      bindingMode: 'registry',
      registryEntryId: mount.id,
      categories: [...(mount.categories ?? [])],
      path: mount.path || null,
      url: mount.url || null,
      role: mount.role,
      authRef: mount.authRef ?? null,
      defaultReadPriority: mount.defaultReadPriority,
    });
  }

  return (
    <div className="niuu-px-4 niuu-py-3 niuu-flex niuu-flex-col niuu-gap-4">
      <div>
        <label className={SECTION_LABEL}>Resource name</label>
        <input
          className={INPUT}
          value={node.label}
          onChange={(e) => onUpdateLabel(node.id, e.target.value)}
        />
      </div>

      <div>
        <label className={SECTION_LABEL}>Binding mode</label>
        <SegmentedFilter
          options={[
            { value: 'registry', label: 'registry' },
            { value: 'ephemeral_local', label: 'ephemeral' },
          ]}
          value={node.bindingMode ?? 'registry'}
          onChange={(mode) =>
            onUpdateNode(node.id, {
              bindingMode: mode,
              registryEntryId:
                mode === 'ephemeral_local'
                  ? null
                  : node.registryEntryId === EPHEMERAL_LOCAL_MOUNT_ID
                    ? null
                    : node.registryEntryId,
              path: mode === 'ephemeral_local' ? null : node.path,
              url: mode === 'ephemeral_local' ? null : node.url,
              role: mode === 'ephemeral_local' ? 'local' : node.role,
              seedFromRegistryId:
                mode === 'ephemeral_local'
                  ? (node.seedFromRegistryId ?? node.registryEntryId)
                  : node.seedFromRegistryId,
            })
          }
          aria-label="Resource binding mode"
          className="niuu-mt-1 niuu-rounded-xl niuu-border niuu-border-border-subtle"
        />
      </div>

      {node.bindingMode === 'ephemeral_local' ? (
        <div className="niuu-flex niuu-flex-col niuu-gap-3">
          <div className="niuu-rounded-md niuu-border niuu-border-status-emerald/40 niuu-bg-status-emerald/10 niuu-p-3 niuu-flex niuu-flex-col niuu-gap-1.5">
            <div className={SECTION_LABEL}>Ephemeral local Mimir</div>
            <div className="niuu-text-xs niuu-text-text-secondary niuu-leading-relaxed">
              This Mimir is created inside the session workspace at runtime. Its local path is fixed
              by the runtime and cannot be changed here.
            </div>
            <div className="niuu-text-[10px] niuu-font-mono niuu-text-text-faint">
              Path: workspace-local (.flock/mimir/local/...)
            </div>
          </div>

          <div>
            <label className={SECTION_LABEL}>Seed from registry mount</label>
            <select
              className={cn(INPUT, 'niuu-mt-0.5')}
              value={node.seedFromRegistryId ?? ''}
              onChange={(e) =>
                onUpdateNode(node.id, { seedFromRegistryId: e.target.value || null })
              }
            >
              <option value="">No seed mount</option>
              {registryMounts.map((mount) => (
                <option key={mount.id} value={mount.id}>
                  {mount.name}
                </option>
              ))}
            </select>
          </div>
        </div>
      ) : (
        <div className="niuu-flex niuu-flex-col niuu-gap-3">
          <div>
            <label className={SECTION_LABEL}>Registry mount</label>
            <select
              className={cn(INPUT, 'niuu-mt-0.5')}
              value={node.registryEntryId ?? ''}
              onChange={(e) => applyRegistryMount(e.target.value)}
            >
              <option value="">Select a Mimir registry mount…</option>
              {registryMounts.map((mount) => (
                <option key={mount.id} value={mount.id}>
                  {mount.name} · {mount.role}
                </option>
              ))}
            </select>
          </div>

          {selectedMount && (
            <div className="niuu-rounded-md niuu-border niuu-border-border-subtle niuu-bg-bg-elevated niuu-p-3 niuu-flex niuu-flex-col niuu-gap-1.5">
              <div className={SECTION_LABEL}>Resolved mount</div>
              <div className="niuu-text-xs niuu-text-text-secondary niuu-leading-relaxed">
                {selectedMount.desc || 'Registry-backed Mimir instance'}
              </div>
              <div className="niuu-flex niuu-flex-wrap niuu-gap-1">
                <span
                  className={cn(
                    TAG,
                    'niuu-border-border-subtle niuu-bg-bg-primary niuu-text-text-primary',
                  )}
                >
                  {selectedMount.role}
                </span>
                <span
                  className={cn(
                    TAG,
                    'niuu-border-border-subtle niuu-bg-bg-primary niuu-text-text-primary',
                  )}
                >
                  {selectedMount.kind}
                </span>
                <span
                  className={cn(
                    TAG,
                    'niuu-border-border-subtle niuu-bg-bg-primary niuu-text-text-primary',
                  )}
                >
                  priority {selectedMount.defaultReadPriority}
                </span>
              </div>
              {(selectedMount.categories ?? []).length > 0 && (
                <div className="niuu-text-[10px] niuu-font-mono niuu-text-text-faint">
                  {(selectedMount.categories ?? []).join(', ')}
                </div>
              )}
              {(selectedMount.path || selectedMount.url) && (
                <div className="niuu-text-[10px] niuu-font-mono niuu-text-text-faint">
                  {selectedMount.path || selectedMount.url}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      <div>
        <label className={SECTION_LABEL}>Categories</label>
        <input
          className={INPUT}
          value={(node.categories ?? []).join(', ')}
          onChange={(e) =>
            onUpdateNode(node.id, {
              categories: e.target.value
                .split(',')
                .map((part) => part.trim())
                .filter(Boolean),
            })
          }
        />
      </div>

      <div className="niuu-flex niuu-flex-col niuu-gap-2">
        <div className="niuu-flex niuu-items-center niuu-justify-between">
          <label className={SECTION_LABEL}>Bindings</label>
          <button
            type="button"
            className={CHIP_BTN}
            onClick={() =>
              onAddResourceBinding(node.id, {
                targetType: 'workflow',
                targetId: workflow.id,
                access: 'read',
                readPriority: node.defaultReadPriority ?? 10,
              })
            }
          >
            + binding
          </button>
        </div>

        {bindings.length === 0 ? (
          <div className="niuu-rounded-md niuu-border niuu-border-border-subtle niuu-bg-bg-elevated niuu-p-3 niuu-text-xs niuu-text-text-muted">
            No workflow bindings yet. The resource exists in the graph, but nothing is attached to
            it.
          </div>
        ) : (
          bindings.map((binding) => (
            <div
              key={binding.id}
              className="niuu-rounded-xl niuu-border niuu-border-border-subtle niuu-bg-bg-elevated niuu-p-3.5 niuu-flex niuu-flex-col niuu-gap-3"
            >
              <div className="niuu-flex niuu-items-center niuu-justify-between">
                <span className="niuu-text-[11px] niuu-font-semibold niuu-text-text-primary">
                  {binding.targetType} binding
                </span>
                <button
                  type="button"
                  className="niuu-bg-transparent niuu-border-none niuu-text-text-faint niuu-cursor-pointer"
                  onClick={() => onRemoveResourceBinding(binding.id)}
                >
                  ×
                </button>
              </div>

              <div className="niuu-grid niuu-grid-cols-2 niuu-gap-2">
                <div>
                  <label className={SECTION_LABEL}>Target type</label>
                  <select
                    className={cn(INPUT, 'niuu-mt-0.5')}
                    value={binding.targetType}
                    onChange={(e) => {
                      const targetType = e.target.value as WorkflowResourceBinding['targetType'];
                      onUpdateResourceBinding(binding.id, {
                        targetType,
                        targetId: defaultTargetIdForType(workflow, targetType),
                      });
                    }}
                  >
                    <option value="workflow">workflow</option>
                    <option value="stage">stage</option>
                    <option value="persona">persona</option>
                  </select>
                </div>
                <div>
                  <label className={SECTION_LABEL}>Access</label>
                  <select
                    className={cn(INPUT, 'niuu-mt-0.5')}
                    value={binding.access}
                    onChange={(e) =>
                      onUpdateResourceBinding(binding.id, {
                        access: e.target.value as WorkflowResourceBinding['access'],
                      })
                    }
                  >
                    <option value="read">read</option>
                    <option value="write">write</option>
                    <option value="read_write">read_write</option>
                  </select>
                </div>
              </div>

              <div>
                <label className={SECTION_LABEL}>Target</label>
                {binding.targetType === 'workflow' ? (
                  <input className={INPUT} value={workflow.name} readOnly />
                ) : binding.targetType === 'stage' ? (
                  <select
                    className={cn(INPUT, 'niuu-mt-0.5')}
                    value={binding.targetId}
                    onChange={(e) =>
                      onUpdateResourceBinding(binding.id, { targetId: e.target.value })
                    }
                  >
                    {stages.map((stage) => (
                      <option key={stage.id} value={stage.id}>
                        {stage.label}
                      </option>
                    ))}
                  </select>
                ) : (
                  <select
                    className={cn(INPUT, 'niuu-mt-0.5')}
                    value={binding.targetId}
                    onChange={(e) =>
                      onUpdateResourceBinding(binding.id, { targetId: e.target.value })
                    }
                  >
                    {personaIds.map((personaId) => (
                      <option key={personaId} value={personaId}>
                        {personaId}
                      </option>
                    ))}
                  </select>
                )}
              </div>

              <div className="niuu-grid niuu-grid-cols-2 niuu-gap-2">
                <div>
                  <label className={SECTION_LABEL}>Read priority</label>
                  <input
                    type="number"
                    className={INPUT}
                    value={binding.readPriority}
                    onChange={(e) =>
                      onUpdateResourceBinding(binding.id, {
                        readPriority: Number.parseInt(e.target.value, 10) || 0,
                      })
                    }
                  />
                </div>
                <div>
                  <label className={SECTION_LABEL}>Write prefixes</label>
                  <input
                    className={INPUT}
                    value={(binding.writePrefixes ?? []).join(', ')}
                    placeholder={isEphemeral ? 'scratch/, notes/' : 'project/, entity/'}
                    onChange={(e) =>
                      onUpdateResourceBinding(binding.id, {
                        writePrefixes: e.target.value
                          .split(',')
                          .map((part) => part.trim())
                          .filter(Boolean),
                      })
                    }
                  />
                </div>
              </div>
            </div>
          ))
        )}
      </div>

      <div className="niuu-border-t niuu-border-border niuu-pt-4">
        <button type="button" className={DELETE_BTN} onClick={() => onDeleteNode(node.id)}>
          Delete node
        </button>
      </div>
    </div>
  );
}

export function WorkflowDetailPanel({
  workflow,
  selectedNode,
  errorCount,
  warnCount,
  issues,
  personas,
  models,
  registryMounts,
  onDeleteNode,
  onUpdateNode,
  onUpdateLabel,
  onUpdateWorkflowMeta,
  onAddPersona,
  onReplacePersona,
  onUpdatePersonaModel,
  onUpdatePersonaBudget,
  onRemovePersona,
  onAddResourceBinding,
  onUpdateResourceBinding,
  onRemoveResourceBinding,
}: WorkflowDetailPanelProps) {
  const title = selectedNode ? selectedNode.label : 'Workflow';
  const subtitle = selectedNode
    ? `${selectedNode.kind} · ${selectedNode.id}`
    : 'Inspector and release summary';

  return (
    <div
      data-testid="workflow-detail-panel"
      className="niuu-w-[300px] niuu-shrink-0 niuu-border-l niuu-border-border niuu-bg-bg-secondary niuu-flex niuu-flex-col niuu-overflow-y-auto"
    >
      <div className="niuu-px-4 niuu-pt-3 niuu-pb-2 niuu-border-b niuu-border-border">
        <div className="niuu-flex niuu-flex-col niuu-gap-0.5">
          <span className="niuu-text-[13px] niuu-font-semibold niuu-text-text-primary niuu-font-sans">
            {title}
          </span>
          <span className="niuu-text-[9px] niuu-font-mono niuu-text-text-faint">{subtitle}</span>
        </div>
      </div>

      {selectedNode?.kind === 'stage' ? (
        <StageInspector
          node={selectedNode}
          workflow={workflow}
          personas={personas}
          models={models}
          issues={issues}
          onDeleteNode={onDeleteNode}
          onUpdateNode={onUpdateNode}
          onUpdateLabel={onUpdateLabel}
          onAddPersona={onAddPersona}
          onReplacePersona={onReplacePersona}
          onUpdatePersonaModel={onUpdatePersonaModel}
          onUpdatePersonaBudget={onUpdatePersonaBudget}
          onRemovePersona={onRemovePersona}
        />
      ) : selectedNode?.kind === 'gate' ? (
        <div className="niuu-px-4 niuu-py-3 niuu-flex niuu-flex-col niuu-gap-4">
          <div>
            <label className={SECTION_LABEL}>Gate name</label>
            <input
              className={INPUT}
              value={selectedNode.label}
              onChange={(e) => onUpdateLabel(selectedNode.id, e.target.value)}
            />
          </div>
          <div>
            <label className={SECTION_LABEL}>Condition</label>
            <textarea
              className={cn(INPUT, 'niuu-min-h-[84px]')}
              value={selectedNode.condition}
              onChange={(e) => onUpdateNode(selectedNode.id, { condition: e.target.value })}
            />
          </div>
          <div>
            <label className={SECTION_LABEL}>Gate mode</label>
            <select
              className={cn(INPUT, 'niuu-mt-0.5')}
              aria-label="Gate mode"
              value={selectedNode.mode ?? 'human_approval'}
              onChange={(e) =>
                onUpdateNode(selectedNode.id, {
                  mode: e.target.value as 'human_approval' | 'human_review' | 'automated_approval',
                })
              }
            >
              <option value="human_approval">human approval</option>
              <option value="human_review">human review</option>
              <option value="automated_approval">automated approval</option>
            </select>
          </div>
          <div>
            <label className={SECTION_LABEL}>Pending behavior</label>
            <select
              className={cn(INPUT, 'niuu-mt-0.5')}
              aria-label="Pending behavior"
              value={selectedNode.pendingBehavior ?? 'help_needed'}
              onChange={(e) =>
                onUpdateNode(selectedNode.id, {
                  pendingBehavior: e.target.value as 'silent' | 'notify_only' | 'help_needed',
                })
              }
            >
              <option value="help_needed">help_needed</option>
              <option value="notify_only">notify_only</option>
              <option value="silent">silent</option>
            </select>
          </div>
          <div>
            <label className={SECTION_LABEL}>Approve event</label>
            <input
              className={INPUT}
              value={selectedNode.approvalEvent ?? ''}
              onChange={(e) =>
                onUpdateNode(selectedNode.id, { approvalEvent: e.target.value.trimStart() })
              }
            />
          </div>
          <div>
            <label className={SECTION_LABEL}>Changes requested event</label>
            <input
              className={INPUT}
              value={selectedNode.changesRequestedEvent ?? ''}
              onChange={(e) =>
                onUpdateNode(selectedNode.id, {
                  changesRequestedEvent: e.target.value.trimStart(),
                })
              }
            />
          </div>
          <div>
            <label className={SECTION_LABEL}>Instructions</label>
            <textarea
              className={cn(INPUT, 'niuu-min-h-[72px]')}
              value={selectedNode.instructions ?? ''}
              onChange={(e) => onUpdateNode(selectedNode.id, { instructions: e.target.value })}
            />
          </div>
          <div>
            <label className={SECTION_LABEL}>Auto-forward after</label>
            <input
              className={INPUT}
              value={selectedNode.autoForwardAfter ?? '30m'}
              onChange={(e) => onUpdateNode(selectedNode.id, { autoForwardAfter: e.target.value })}
            />
          </div>
        </div>
      ) : selectedNode?.kind === 'cond' ? (
        <div className="niuu-px-4 niuu-py-3 niuu-flex niuu-flex-col niuu-gap-4">
          <div>
            <label className={SECTION_LABEL}>Condition name</label>
            <input
              className={INPUT}
              value={selectedNode.label}
              onChange={(e) => onUpdateLabel(selectedNode.id, e.target.value)}
            />
          </div>
          <div>
            <label className={SECTION_LABEL}>Expression</label>
            <textarea
              className={cn(INPUT, 'niuu-min-h-[120px] niuu-font-mono')}
              value={selectedNode.predicate}
              onChange={(e) => onUpdateNode(selectedNode.id, { predicate: e.target.value })}
            />
          </div>
        </div>
      ) : selectedNode?.kind === 'trigger' ? (
        <div className="niuu-px-4 niuu-py-3 niuu-flex niuu-flex-col niuu-gap-4">
          <div>
            <label className={SECTION_LABEL}>Trigger label</label>
            <input
              className={INPUT}
              value={selectedNode.label}
              onChange={(e) => onUpdateLabel(selectedNode.id, e.target.value)}
            />
          </div>
          <div>
            <label className={SECTION_LABEL}>Dispatch event</label>
            <select
              className={cn(INPUT, 'niuu-font-mono')}
              value={selectedNode.dispatchEvent ?? 'code.requested'}
              onChange={(e) => onUpdateNode(selectedNode.id, { dispatchEvent: e.target.value })}
            >
              {triggerEventOptions(personas, selectedNode.dispatchEvent ?? 'code.requested').map(
                (eventType) => (
                  <option key={eventType} value={eventType}>
                    {eventType}
                  </option>
                ),
              )}
            </select>
          </div>
          <div>
            <label className={SECTION_LABEL}>Trigger source</label>
            <input
              className={cn(INPUT, 'niuu-font-mono')}
              value={selectedNode.source ?? 'manual dispatch'}
              onChange={(e) => onUpdateNode(selectedNode.id, { source: e.target.value })}
            />
          </div>
        </div>
      ) : selectedNode?.kind === 'end' ? (
        <div className="niuu-px-4 niuu-py-3 niuu-flex niuu-flex-col niuu-gap-4">
          <div>
            <label className={SECTION_LABEL}>End label</label>
            <input
              className={INPUT}
              value={selectedNode.label}
              onChange={(e) => onUpdateLabel(selectedNode.id, e.target.value)}
            />
          </div>
          <div className="niuu-rounded-md niuu-border niuu-border-border-subtle niuu-bg-bg-elevated niuu-p-3 niuu-text-xs niuu-text-text-secondary">
            Terminal node. Use this to make completion paths explicit in the graph and pipeline
            views.
          </div>
        </div>
      ) : selectedNode?.kind === 'resource' ? (
        <ResourceInspector
          node={selectedNode}
          workflow={workflow}
          registryMounts={registryMounts}
          onUpdateNode={onUpdateNode}
          onUpdateLabel={onUpdateLabel}
          onDeleteNode={onDeleteNode}
          onAddResourceBinding={onAddResourceBinding}
          onUpdateResourceBinding={onUpdateResourceBinding}
          onRemoveResourceBinding={onRemoveResourceBinding}
        />
      ) : (
        <WorkflowSummary
          workflow={workflow}
          errorCount={errorCount}
          warnCount={warnCount}
          onUpdateWorkflowMeta={onUpdateWorkflowMeta}
        />
      )}
    </div>
  );
}
