import { useState } from 'react';
import { cn, SegmentedFilter } from '@niuulabs/ui';
import type {
  Workflow,
  WorkflowGateMode,
  WorkflowNode,
  WorkflowResourceBinding,
  WorkflowResourceNode,
  WorkflowStageNode,
  WorkflowSubworkflowNode,
} from '../../domain/workflow';
import type { WorkflowIssue } from '../../domain/workflowValidation';
import { parseWorkflowEdgeLabel } from '../../domain/workflowSemantics';
import type { WorkflowBuilderActions, WorkflowStageModelOption } from './useWorkflowBuilder';
import type { PersonaEntry } from './LibraryPanel';
import { normalizedStageMembers } from './graphUtils';
import { EPHEMERAL_LOCAL_MOUNT_ID, type WorkflowRegistryMount } from './mimirRegistry';
import { EvidencePolicyEditor, emptyEvidencePolicy } from './EvidencePolicyEditor';

export interface WorkflowDetailPanelProps {
  workflow: Workflow;
  /** View mode keeps inspection available while disabling mutations. */
  readOnly?: boolean;
  selectedNode: WorkflowNode | null;
  errorCount: number;
  warnCount: number;
  issues: WorkflowIssue[];
  personas: PersonaEntry[];
  models: WorkflowStageModelOption[];
  registryMounts: WorkflowRegistryMount[];
  onDeleteNode: WorkflowBuilderActions['deleteNode'];
  onUpdateNode: WorkflowBuilderActions['updateNode'];
  onSelectSubworkflowTemplate: WorkflowBuilderActions['selectSubworkflowTemplate'];
  onAddSubworkflowTemplate: WorkflowBuilderActions['addSubworkflowTemplate'];
  onRenameSubworkflowTemplate: WorkflowBuilderActions['renameSubworkflowTemplate'];
  onRemoveSubworkflowTemplate: WorkflowBuilderActions['removeSubworkflowTemplate'];
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
  workflows?: Workflow[];
  onOpenWorkflow?: (workflow: Workflow) => void;
  /** Closes the panel. Omit to render without a close control (e.g. a fixed
   *  embed that has nowhere else to put the panel). */
  onClose?: () => void;
}

const SECTION_LABEL =
  'niuu:text-[9px] niuu:font-semibold niuu:uppercase niuu:tracking-[0.22em] niuu:text-text-faint niuu:font-mono';
const INPUT =
  'niuu:w-full niuu:py-2.5 niuu:px-3.5 niuu:bg-bg-tertiary niuu:border niuu:border-border-subtle niuu:rounded-lg niuu:text-text-primary niuu:font-sans niuu:text-[12px]';
const CHIP_BTN =
  'niuu:rounded-md niuu:border niuu:px-2 niuu:py-1 niuu:text-[11px] niuu:font-mono niuu:transition-colors';
const TAG =
  'niuu:inline-flex niuu:items-center niuu:rounded-md niuu:border niuu:px-2 niuu:py-0.5 niuu:text-[10px] niuu:font-mono';
const TAB_BTN =
  'niuu:px-0 niuu:py-2 niuu:bg-transparent niuu:border-none niuu:border-b-2 niuu:text-[11px] niuu:font-mono niuu:uppercase niuu:tracking-[0.18em]';
const DELETE_BTN =
  'niuu:inline-flex niuu:items-center niuu:rounded-lg niuu:border niuu:border-critical/60 niuu:bg-critical-bg/25 niuu:px-2.5 niuu:py-1.5 niuu:text-[12px] niuu:font-semibold niuu:text-critical';

export function issueTone(severity: WorkflowIssue['severity']) {
  return severity === 'error'
    ? 'niuu:border-critical/40 niuu:bg-critical-bg/30 niuu:text-critical'
    : 'niuu:border-status-amber/40 niuu:bg-status-amber/10 niuu:text-status-amber';
}

function personaById(personas: PersonaEntry[], id: string) {
  return personas.find((persona) => persona.id === id);
}

export function personaGlyph(role?: string) {
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

export function memberIssuesForPersona(issues: WorkflowIssue[], persona: PersonaEntry | undefined) {
  if (!persona) return [];
  const keys = [persona.label, ...(persona.produces ?? []), ...(persona.consumes ?? [])].map((v) =>
    v.toLowerCase(),
  );
  return issues.filter((issue) => keys.some((key) => issue.message.toLowerCase().includes(key)));
}

export function triggerEventOptions(personas: PersonaEntry[], current: string): string[] {
  return [
    ...new Set(
      [
        ...personas.flatMap((persona) => persona.consumes ?? []),
        current || 'code.requested',
      ].filter(Boolean),
    ),
  ].sort();
}

export function uniquePersonaIds(workflow: Workflow): string[] {
  return [
    ...new Set(
      workflow.nodes.flatMap((node) => {
        if (node.kind !== 'stage') return [];
        return normalizedStageMembers(node).map((member) => member.personaId);
      }),
    ),
  ];
}

export function defaultTargetIdForType(
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
  const waitCount = workflow.nodes.filter((n) => n.kind === 'wait').length;
  const endCount = workflow.nodes.filter((n) => n.kind === 'end').length;

  return (
    <div className="niuu:px-4 niuu:py-3 niuu:flex niuu:flex-col niuu:gap-4">
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
          className={cn(INPUT, 'niuu:min-h-[84px] niuu:leading-relaxed')}
          value={workflow.description ?? ''}
          onChange={(e) => onUpdateWorkflowMeta({ description: e.target.value })}
        />
      </div>

      <div className="niuu:grid niuu:grid-cols-2 niuu:gap-2">
        <div className="niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-elevated niuu:p-3">
          <div className={SECTION_LABEL}>Version</div>
          <input
            className={cn(INPUT, 'niuu:mt-1')}
            value={workflow.version ?? '0.1.0'}
            readOnly
            title="The server assigns the next version when this workflow is saved."
          />
          <div className="niuu:mt-1 niuu:text-[9px] niuu:text-text-faint">Assigned on save</div>
        </div>
        <div className="niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-elevated niuu:p-3">
          <div className={SECTION_LABEL}>Summary</div>
          <div className="niuu:mt-2 niuu:text-xs niuu:text-text-secondary niuu:leading-relaxed">
            {triggerCount} triggers · {stageCount} stages · {gateCount} gates · {condCount}{' '}
            conditions · {waitCount} waits · {endCount} end nodes · {workflow.edges.length} edges
          </div>
        </div>
      </div>

      <div>
        <label className={SECTION_LABEL}>Tags</label>
        <input
          className={INPUT}
          value={(workflow.tags ?? []).join(', ')}
          onChange={(e) =>
            onUpdateWorkflowMeta({
              tags: e.target.value
                .split(',')
                .map((item) => item.trim().toLowerCase())
                .filter(Boolean),
            })
          }
          placeholder="research, campaign, review"
        />
      </div>

      <div className="niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-elevated niuu:p-3 niuu:flex niuu:gap-2">
        <span
          className={cn(
            CHIP_BTN,
            errorCount
              ? 'niuu:border-critical niuu:text-critical'
              : 'niuu:border-border niuu:text-text-faint',
          )}
        >
          ERR {errorCount}
        </span>
        <span
          className={cn(
            CHIP_BTN,
            warnCount
              ? 'niuu:border-status-amber niuu:text-status-amber'
              : 'niuu:border-border niuu:text-text-faint',
          )}
        >
          WARN {warnCount}
        </span>
      </div>
    </div>
  );
}

function catalogSelectionValue(workflow: Workflow): string {
  return `catalog:${workflow.id}:${workflow.documentRevision ?? ''}`;
}

/** Resolve a template's alias to a catalog candidate and whether that
 *  candidate is the exact pinned revision, the same lookup the panel used to
 *  do once for a node's single dependency, now per template. */
function resolveTemplateCatalogChild(
  workflow: Workflow,
  workflows: Workflow[],
  alias: string,
): { catalogChildWorkflow: Workflow | null; childWorkflowExact: boolean } {
  const dependency = alias ? workflow.workflowDependencies?.[alias] : undefined;
  const catalogChildWorkflow =
    (dependency
      ? workflows.find((candidate) => candidate.id === dependency.id)
      : workflows.find((candidate) => candidate.id === alias || candidate.name === alias)) ?? null;
  const childWorkflowExact = Boolean(
    catalogChildWorkflow &&
    dependency &&
    catalogChildWorkflow.documentRevision === dependency.digest,
  );
  return { catalogChildWorkflow, childWorkflowExact };
}

function SubworkflowInspector({
  node,
  workflow,
  workflows,
  personas,
  onSelectSubworkflowTemplate,
  onAddSubworkflowTemplate,
  onRenameSubworkflowTemplate,
  onRemoveSubworkflowTemplate,
  onUpdateNode,
  onUpdateLabel,
  onDeleteNode,
}: {
  node: WorkflowSubworkflowNode;
  workflow: Workflow;
  workflows: Workflow[];
  personas: PersonaEntry[];
  onSelectSubworkflowTemplate: WorkflowBuilderActions['selectSubworkflowTemplate'];
  onAddSubworkflowTemplate: WorkflowBuilderActions['addSubworkflowTemplate'];
  onRenameSubworkflowTemplate: WorkflowBuilderActions['renameSubworkflowTemplate'];
  onRemoveSubworkflowTemplate: WorkflowBuilderActions['removeSubworkflowTemplate'];
  onUpdateNode: WorkflowBuilderActions['updateNode'];
  onUpdateLabel: WorkflowBuilderActions['updateNodeLabel'];
  onDeleteNode: WorkflowBuilderActions['deleteNode'];
}) {
  const templateEntries = Object.entries(node.templates ?? {});
  const templateNames = templateEntries.map(([templateName]) => templateName);
  const hasSeveralTemplates = templateEntries.length > 1;
  const candidates = workflows.filter((candidate) => candidate.id !== workflow.id);
  const coordinatorAliases = Object.keys(workflow.personaDependencies ?? {}).sort();
  const outgoing = workflow.edges.filter((edge) => edge.source === node.id);
  const joinedEvents = [
    ...new Set(
      outgoing
        .map((edge) => parseWorkflowEdgeLabel(edge.label)?.sourceEventType)
        .filter((eventType): eventType is string => Boolean(eventType)),
    ),
  ];
  const coordinatorName = (alias: string) =>
    personas.find((persona) => persona.id === (workflow.personaDependencies?.[alias]?.id ?? alias))
      ?.label ?? alias;

  function selectChildFor(templateName: string, value: string) {
    const selected = candidates.find((candidate) => catalogSelectionValue(candidate) === value);
    if (selected?.documentRevision) {
      onSelectSubworkflowTemplate(node.id, templateName, selected);
    }
  }

  /** Why "Remove" is disabled for this template, or null when it may be
   *  removed. Mirrors `removeSubworkflowTemplate`'s own refusals so the
   *  control never offers an action the domain would reject. */
  function templateRemovalBlockedReason(templateName: string): string | null {
    if (!hasSeveralTemplates) return 'This node offers only one template.';
    const usedBy = (node.children ?? []).filter(
      (declaredChild) => declaredChild.template === templateName,
    );
    if (usedBy.length > 0) {
      return `Used by declared child ${usedBy.map((declaredChild) => declaredChild.key).join(', ')}.`;
    }
    return null;
  }

  function boundedValue(value: string, minimum: number, maximum: number): number {
    return Math.min(maximum, Math.max(minimum, Number(value) || minimum));
  }

  function renderChildSelect(templateName: string, alias: string, selectId: string) {
    const dependency = alias ? workflow.workflowDependencies?.[alias] : undefined;
    const { catalogChildWorkflow, childWorkflowExact } = resolveTemplateCatalogChild(
      workflow,
      workflows,
      alias,
    );
    const exactCatalogValue =
      catalogChildWorkflow && childWorkflowExact
        ? catalogSelectionValue(catalogChildWorkflow)
        : null;
    const selectedValue = exactCatalogValue ?? (dependency ? `pinned:${dependency.digest}` : '');

    return (
      <>
        <select
          id={selectId}
          data-testid={
            hasSeveralTemplates ? `child-workflow-select-${templateName}` : 'child-workflow-select'
          }
          className={INPUT}
          value={selectedValue}
          onChange={(event) => selectChildFor(templateName, event.target.value)}
        >
          {!dependency ? <option value="">Choose a workflow…</option> : null}
          {dependency && !exactCatalogValue ? (
            <option value={`pinned:${dependency.digest}`}>Current saved child</option>
          ) : null}
          {candidates.map((candidate) => {
            const revision = candidate.documentRevision?.trim();
            return (
              <option
                key={`${candidate.id}:${revision ?? 'unavailable'}`}
                value={catalogSelectionValue(candidate)}
                disabled={!revision}
              >
                {candidate.name} · {candidate.version ?? 'unversioned'}
                {!revision ? ' · not ready to use' : ''}
              </option>
            );
          })}
        </select>
        {dependency ? (
          <details className="niuu:mt-2 niuu:text-[10px] niuu:text-text-faint">
            <summary className="niuu:cursor-pointer">Version details</summary>
            <div
              data-testid="child-workflow-pin"
              className="niuu:mt-1 niuu:break-all niuu:font-mono"
            >
              {alias} · {dependency.digest}
            </div>
          </details>
        ) : (
          <p className="niuu:mt-2 niuu:text-[11px] niuu:text-critical">
            Choose a child workflow before saving.
          </p>
        )}
        {dependency && !childWorkflowExact ? (
          <p className="niuu:mt-2 niuu:text-[11px] niuu:text-text-faint">
            This exact pinned revision is not in the loaded catalog. Selecting the current catalog
            version will explicitly repin it.
          </p>
        ) : null}
      </>
    );
  }

  return (
    <section className="niuu:space-y-4 niuu:px-4 niuu:py-3 niuu:text-xs niuu:text-text-secondary">
      <div>
        <label className={SECTION_LABEL} htmlFor={`subworkflow-${node.id}-name`}>
          Name
        </label>
        <input
          id={`subworkflow-${node.id}-name`}
          className={INPUT}
          value={node.label}
          onChange={(event) => onUpdateLabel(node.id, event.target.value)}
        />
      </div>

      {!hasSeveralTemplates ? (
        <div>
          <div className="niuu:flex niuu:items-center niuu:justify-between">
            <label className={SECTION_LABEL} htmlFor={`subworkflow-${node.id}-child`}>
              Child workflow
            </label>
            <button
              type="button"
              data-testid={`subworkflow-${node.id}-add-template`}
              className="niuu:font-mono niuu:text-[9px] niuu:uppercase niuu:tracking-[0.18em] niuu:text-text-faint niuu:hover:text-text-primary niuu:bg-transparent niuu:border-none"
              onClick={() => onAddSubworkflowTemplate(node.id)}
            >
              + Offer another child
            </button>
          </div>
          {renderChildSelect(
            templateEntries[0]?.[0] ?? 'default',
            templateEntries[0]?.[1] ?? '',
            `subworkflow-${node.id}-child`,
          )}
        </div>
      ) : (
        <div className="niuu:space-y-3">
          <div className="niuu:flex niuu:items-center niuu:justify-between">
            <span className={SECTION_LABEL}>Child workflows</span>
            <button
              type="button"
              data-testid={`subworkflow-${node.id}-add-template`}
              className={CHIP_BTN}
              onClick={() => onAddSubworkflowTemplate(node.id)}
            >
              + Add template
            </button>
          </div>
          {templateEntries.map(([templateName, alias]) => {
            const selectId = `subworkflow-${node.id}-child-${templateName}`;
            const blockedReason = templateRemovalBlockedReason(templateName);
            return (
              <div
                key={templateName}
                className="niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-elevated niuu:p-3 niuu:space-y-2"
              >
                <div className="niuu:flex niuu:items-center niuu:gap-2">
                  <input
                    key={templateName}
                    defaultValue={templateName}
                    aria-label={`Template name for ${templateName}`}
                    data-testid={`subworkflow-${node.id}-template-name-${templateName}`}
                    className={cn(INPUT, 'niuu:flex-1')}
                    onBlur={(event) => {
                      const trimmed = event.target.value.trim();
                      if (!trimmed || trimmed === templateName || templateNames.includes(trimmed)) {
                        event.target.value = templateName;
                        return;
                      }
                      onRenameSubworkflowTemplate(node.id, templateName, trimmed);
                    }}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter') {
                        event.preventDefault();
                        event.currentTarget.blur();
                      }
                    }}
                  />
                  <button
                    type="button"
                    data-testid={`subworkflow-${node.id}-remove-template-${templateName}`}
                    className="niuu:bg-transparent niuu:border-none niuu:text-text-faint niuu:text-xl niuu:leading-none niuu:disabled:opacity-40"
                    disabled={Boolean(blockedReason)}
                    title={blockedReason ?? `Remove template ${templateName}`}
                    onClick={() => onRemoveSubworkflowTemplate(node.id, templateName)}
                  >
                    ×
                  </button>
                </div>
                <label className="niuu:sr-only" htmlFor={selectId}>
                  {`Child workflow for ${templateName}`}
                </label>
                {renderChildSelect(templateName, alias, selectId)}
              </div>
            );
          })}
        </div>
      )}

      <div>
        <label className={SECTION_LABEL} htmlFor={`subworkflow-${node.id}-coordinator`}>
          Coordinator
        </label>
        <select
          id={`subworkflow-${node.id}-coordinator`}
          data-testid="subworkflow-coordinator"
          className={INPUT}
          value={node.allowedCoordinator}
          disabled={coordinatorAliases.length === 0}
          onChange={(event) => onUpdateNode(node.id, { allowedCoordinator: event.target.value })}
        >
          {!node.allowedCoordinator ? <option value="">Choose a coordinator…</option> : null}
          {node.allowedCoordinator && !coordinatorAliases.includes(node.allowedCoordinator) ? (
            <option value={node.allowedCoordinator} disabled>
              {node.allowedCoordinator} · dependency missing
            </option>
          ) : null}
          {coordinatorAliases.map((alias) => (
            <option key={alias} value={alias}>
              {coordinatorName(alias)} · {alias}
            </option>
          ))}
        </select>
        {coordinatorAliases.length === 0 ? (
          <p className="niuu:mt-2 niuu:text-[11px] niuu:text-critical">
            No coordinator personas are available in this workflow. Add and save a persona
            dependency first.
          </p>
        ) : null}
      </div>

      <div className="niuu:grid niuu:grid-cols-3 niuu:gap-2">
        <div>
          <label className={SECTION_LABEL} htmlFor={`subworkflow-${node.id}-max-children`}>
            Children
          </label>
          <input
            id={`subworkflow-${node.id}-max-children`}
            data-testid="subworkflow-max-children"
            className={INPUT}
            type="number"
            min={1}
            max={100}
            value={node.maxChildren}
            onChange={(event) => {
              const maxChildren = boundedValue(event.target.value, 1, 100);
              onUpdateNode(node.id, {
                maxChildren,
                maxActiveChildren: Math.min(node.maxActiveChildren ?? maxChildren, maxChildren),
              });
            }}
          />
        </div>
        <div>
          <label className={SECTION_LABEL} htmlFor={`subworkflow-${node.id}-max-active`}>
            Active
          </label>
          <input
            id={`subworkflow-${node.id}-max-active`}
            data-testid="subworkflow-max-active"
            className={INPUT}
            type="number"
            min={1}
            max={node.maxChildren}
            value={node.maxActiveChildren ?? node.maxChildren}
            onChange={(event) =>
              onUpdateNode(node.id, {
                maxActiveChildren: boundedValue(event.target.value, 1, node.maxChildren),
              })
            }
          />
        </div>
        <div>
          <label className={SECTION_LABEL} htmlFor={`subworkflow-${node.id}-max-attempts`}>
            Attempts
          </label>
          <input
            id={`subworkflow-${node.id}-max-attempts`}
            data-testid="subworkflow-max-attempts"
            className={INPUT}
            type="number"
            min={1}
            max={10}
            value={node.maxAttempts}
            onChange={(event) =>
              onUpdateNode(node.id, {
                maxAttempts: boundedValue(event.target.value, 1, 10),
              })
            }
          />
        </div>
      </div>

      <div>
        <label className={SECTION_LABEL} htmlFor={`subworkflow-${node.id}-blocked-event`}>
          Blocked event
        </label>
        <input
          id={`subworkflow-${node.id}-blocked-event`}
          data-testid="subworkflow-blocked-event"
          className={INPUT}
          value={node.blockedEvent ?? ''}
          placeholder="children.blocked"
          onChange={(event) => onUpdateNode(node.id, { blockedEvent: event.target.value })}
        />
        <p className="niuu:mt-2 niuu:text-[11px] niuu:text-text-faint">
          Sent to the coordinator when a child cannot continue.
        </p>
      </div>

      <div className="niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-elevated niuu:p-3">
        <div className={SECTION_LABEL}>Joined continuation</div>
        {outgoing.length > 0 && joinedEvents.length === 1 ? (
          <p className="niuu:mt-2 niuu:font-mono niuu:text-[11px] niuu:text-text-secondary">
            {joinedEvents[0]}
          </p>
        ) : (
          <p className="niuu:mt-2 niuu:text-[11px] niuu:text-critical">
            Connect this node to a next step and use one shared output event across every outgoing
            edge. Use children.completed for a generic continuation.
          </p>
        )}
        {joinedEvents.includes(node.blockedEvent ?? '') ? (
          <p className="niuu:mt-2 niuu:text-[11px] niuu:text-critical">
            The blocked event must differ from the joined continuation event.
          </p>
        ) : null}
      </div>

      <details>
        <summary>Input contract</summary>
        <pre className="niuu:overflow-auto">{JSON.stringify(node.inputSchema, null, 2)}</pre>
      </details>
      <details>
        <summary>Result contract</summary>
        <pre className="niuu:overflow-auto">{JSON.stringify(node.resultSchema, null, 2)}</pre>
      </details>

      <div className="niuu:border-t niuu:border-border niuu:pt-4">
        <button type="button" className={DELETE_BTN} onClick={() => onDeleteNode(node.id)}>
          Delete node
        </button>
      </div>
    </section>
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
  const effectiveNewModelId = newModelId || defaultModelId;
  const executionOptions = [
    { value: 'parallel' as const, label: 'parallel' },
    { value: 'sequential' as const, label: 'sequential' },
  ];
  const availablePersonas = personas.filter(
    (persona) => !stageMembers.some((member) => member.personaId === persona.id),
  );

  return (
    <div className="niuu:px-4 niuu:py-0 niuu:flex niuu:flex-col niuu:gap-4">
      <div className="niuu:flex niuu:items-center niuu:justify-between niuu:py-4 niuu:border-b niuu:border-border niuu:mx-[-16px] niuu:px-4">
        <div className="niuu:flex niuu:items-center niuu:gap-2.5">
          <span className="niuu:text-[18px] niuu:text-text-primary">◆</span>
          <span className="niuu:text-[16px] niuu:font-semibold niuu:text-text-primary">
            {node.kind === 'stage' ? 'Stage' : node.label}
          </span>
        </div>
        <span className="niuu:text-[11px] niuu:font-mono niuu:text-text-faint">{node.id}</span>
      </div>
      <div className="niuu:flex niuu:gap-5 niuu:pb-1 niuu:mb-1 niuu:border-b niuu:border-border niuu:mx-[-16px] niuu:px-4">
        {(['config', 'flock', 'validate'] as const).map((name) => (
          <button
            key={name}
            type="button"
            onClick={() => setTab(name)}
            className={cn(
              TAB_BTN,
              tab === name
                ? 'niuu:border-text-primary niuu:text-text-primary'
                : 'niuu:border-transparent niuu:text-text-faint',
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
              className="niuu:mt-1 niuu:rounded-xl niuu:border niuu:border-border-subtle"
            />
          </div>

          <div className="niuu:grid niuu:grid-cols-2 niuu:gap-2">
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
            <div className="niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-elevated niuu:p-3 niuu:flex niuu:flex-col niuu:gap-2">
              <div className={SECTION_LABEL}>Fan-in / fan-out</div>
              <div className="niuu:text-xs niuu:text-text-secondary">
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
                className="niuu:rounded-xl niuu:border niuu:border-border-subtle"
              />
            </div>
          )}

          <div className="niuu:border-t niuu:border-border niuu:pt-4">
            <button type="button" className={DELETE_BTN} onClick={() => onDeleteNode(node.id)}>
              Delete node
            </button>
          </div>
        </>
      )}

      {tab === 'flock' && (
        <>
          <div className={SECTION_LABEL}>Personas in this stage</div>
          <div className="niuu:flex niuu:flex-col niuu:gap-1.5">
            {stageMembers.length === 0 && (
              <div className="niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-elevated niuu:p-3 niuu:text-xs niuu:text-text-muted">
                No ravns assigned yet.
              </div>
            )}
            {stageMembers.map((member) => {
              const persona = personaById(personas, member.personaId);
              const memberIssues = memberIssuesForPersona(nodeIssues, persona);
              return (
                <div
                  key={member.personaId}
                  className="niuu:rounded-xl niuu:border niuu:border-border-subtle niuu:bg-bg-elevated niuu:p-3.5 niuu:flex niuu:flex-col niuu:gap-2.5"
                >
                  <div className="niuu:flex niuu:items-start niuu:gap-2">
                    <div className="niuu:flex niuu:h-8 niuu:w-8 niuu:items-center niuu:justify-center niuu:rounded-full niuu:border niuu:border-brand niuu:text-brand niuu:font-mono niuu:text-[18px]">
                      {personaGlyph(persona?.role)}
                    </div>
                    <div className="niuu:flex-1 niuu:min-w-0">
                      <div className="niuu:text-[13px] niuu:font-semibold niuu:text-text-primary">
                        {persona?.label ?? member.personaId}
                      </div>
                      <div className="niuu:text-[9px] niuu:font-mono niuu:text-text-faint niuu:flex niuu:flex-wrap niuu:gap-2">
                        <span>{persona?.role ?? 'unknown'}</span>
                        <span>{member.model || 'no model'}</span>
                      </div>
                    </div>
                    <button
                      type="button"
                      className="niuu:bg-transparent niuu:border-none niuu:text-text-faint niuu:text-xl niuu:leading-none"
                      onClick={() => onRemovePersona(node.id, member.personaId)}
                    >
                      ×
                    </button>
                  </div>

                  <div className="niuu:flex niuu:items-center niuu:gap-2.5">
                    <span className="niuu:text-[10px] niuu:text-text-muted">budget</span>
                    <input
                      type="number"
                      className="niuu:w-14 niuu:bg-transparent niuu:border-none niuu:p-0 niuu:text-[14px] niuu:font-semibold niuu:text-text-primary"
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
                      className={cn(INPUT, 'niuu:mt-0.5')}
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

                  <div className="niuu:grid niuu:grid-cols-1 niuu:gap-2">
                    <div>
                      <div className={SECTION_LABEL}>Consumes</div>
                      <div className="niuu:flex niuu:flex-wrap niuu:gap-1 niuu:mt-0.5">
                        {(persona?.consumes ?? []).map((event) => (
                          <span
                            key={event}
                            className={cn(
                              TAG,
                              'niuu:border-transparent niuu:bg-brand/20 niuu:text-brand',
                            )}
                          >
                            {event}
                          </span>
                        ))}
                      </div>
                    </div>
                    <div>
                      <div className={SECTION_LABEL}>Outcomes</div>
                      {Object.keys(persona?.outcomeEvents ?? {}).length > 0 ? (
                        <div className="niuu:mt-1 niuu:flex niuu:flex-col niuu:gap-1">
                          {Object.entries(persona?.outcomeEvents ?? {}).map(
                            ([outcome, eventType]) => (
                              <div
                                key={outcome}
                                className={cn(
                                  'niuu:flex niuu:items-center niuu:justify-between niuu:gap-2 niuu:rounded-md niuu:border niuu:px-2 niuu:py-1.5',
                                  memberIssues.length > 0
                                    ? 'niuu:border-critical/60 niuu:bg-critical-bg niuu:text-critical'
                                    : 'niuu:border-border-subtle niuu:bg-bg-primary niuu:text-text-secondary',
                                )}
                              >
                                <span className="niuu:text-[10px] niuu:font-semibold">
                                  {outcome.replaceAll('_', ' ')}
                                </span>
                                <span className="niuu:truncate niuu:font-mono niuu:text-[9px]">
                                  {eventType}
                                </span>
                              </div>
                            ),
                          )}
                        </div>
                      ) : (
                        <div className="niuu:flex niuu:flex-wrap niuu:gap-1 niuu:mt-0.5">
                          {(persona?.produces ?? []).map((event) => (
                            <span
                              key={event}
                              className={cn(
                                TAG,
                                memberIssues.length > 0
                                  ? 'niuu:border-critical niuu:border-dashed niuu:bg-critical-bg niuu:text-critical'
                                  : 'niuu:border-transparent niuu:bg-bg-primary niuu:text-text-primary',
                              )}
                            >
                              {event}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>

                  {memberIssues.length > 0 && (
                    <div className="niuu:rounded-lg niuu:border niuu:border-critical niuu:bg-critical-bg niuu:p-3 niuu:text-critical">
                      <div className="niuu:text-[12px] niuu:font-semibold niuu:leading-snug">
                        {memberIssues[0]?.message}
                      </div>
                    </div>
                  )}

                  <div>
                    <div className={SECTION_LABEL}>Ravn</div>
                    <select
                      className={cn(INPUT, 'niuu:mt-0.5')}
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

          <div className="niuu:border-t niuu:border-border niuu:pt-6">
            <label className={SECTION_LABEL}>Add persona</label>
            <div className="niuu:grid niuu:grid-cols-1 niuu:gap-2 niuu:mt-0.5">
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
                value={effectiveNewModelId}
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
                disabled={!newPersonaId || !effectiveNewModelId}
                onClick={() => {
                  if (!newPersonaId || !effectiveNewModelId) return;
                  onAddPersona(node.id, newPersonaId, effectiveNewModelId, 40);
                  setNewPersonaId('');
                  setNewModelId(defaultModelId);
                }}
              >
                Add ravn
              </button>
            </div>
          </div>

          <div className="niuu:border-t niuu:border-border niuu:pt-4">
            <button type="button" className={DELETE_BTN} onClick={() => onDeleteNode(node.id)}>
              Delete node
            </button>
          </div>
        </>
      )}

      {tab === 'validate' && (
        <div className="niuu:flex niuu:flex-col niuu:gap-2">
          {nodeIssues.length === 0 ? (
            <div className="niuu:rounded-md niuu:border niuu:border-status-emerald/40 niuu:bg-status-emerald/10 niuu:p-3 niuu:text-xs niuu:text-status-emerald">
              No validation issues on this node.
            </div>
          ) : (
            nodeIssues.map((issue, index) => (
              <div
                key={`${issue.kind}-${index}`}
                className={cn(
                  'niuu:rounded-xl niuu:border niuu:p-4 niuu:text-sm niuu:leading-relaxed',
                  issueTone(issue.severity),
                )}
              >
                <div className="niuu:font-semibold niuu:mb-1">{issue.kind}</div>
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
      adapter: mount.adapter ?? '',
      kwargs: mount.kwargs ?? {},
      secretKwargsEnv: mount.secretKwargsEnv ?? {},
      authRef: mount.authRef ?? null,
      defaultReadPriority: mount.defaultReadPriority,
    });
  }

  return (
    <div className="niuu:px-4 niuu:py-3 niuu:flex niuu:flex-col niuu:gap-4">
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
          className="niuu:mt-1 niuu:rounded-xl niuu:border niuu:border-border-subtle"
        />
      </div>

      {node.bindingMode === 'ephemeral_local' ? (
        <div className="niuu:flex niuu:flex-col niuu:gap-3">
          <div className="niuu:rounded-md niuu:border niuu:border-status-emerald/40 niuu:bg-status-emerald/10 niuu:p-3 niuu:flex niuu:flex-col niuu:gap-1.5">
            <div className={SECTION_LABEL}>Ephemeral local Mimir</div>
            <div className="niuu:text-xs niuu:text-text-secondary niuu:leading-relaxed">
              This Mimir is created inside the session workspace at runtime. Its local path is fixed
              by the runtime and cannot be changed here.
            </div>
            <div className="niuu:text-[10px] niuu:font-mono niuu:text-text-faint">
              Path: workspace-local (.flock/mimir/local/...)
            </div>
          </div>

          <div>
            <label className={SECTION_LABEL}>Seed from registry mount</label>
            <select
              className={cn(INPUT, 'niuu:mt-0.5')}
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
        <div className="niuu:flex niuu:flex-col niuu:gap-3">
          <div>
            <label className={SECTION_LABEL}>Registry mount</label>
            <select
              className={cn(INPUT, 'niuu:mt-0.5')}
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
            <div className="niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-elevated niuu:p-3 niuu:flex niuu:flex-col niuu:gap-1.5">
              <div className={SECTION_LABEL}>Resolved mount</div>
              <div className="niuu:text-xs niuu:text-text-secondary niuu:leading-relaxed">
                {selectedMount.desc || 'Registry-backed Mimir instance'}
              </div>
              <div className="niuu:flex niuu:flex-wrap niuu:gap-1">
                <span
                  className={cn(
                    TAG,
                    'niuu:border-border-subtle niuu:bg-bg-primary niuu:text-text-primary',
                  )}
                >
                  {selectedMount.role}
                </span>
                <span
                  className={cn(
                    TAG,
                    'niuu:border-border-subtle niuu:bg-bg-primary niuu:text-text-primary',
                  )}
                >
                  {selectedMount.kind}
                </span>
                <span
                  className={cn(
                    TAG,
                    'niuu:border-border-subtle niuu:bg-bg-primary niuu:text-text-primary',
                  )}
                >
                  priority {selectedMount.defaultReadPriority}
                </span>
              </div>
              {(selectedMount.categories ?? []).length > 0 && (
                <div className="niuu:text-[10px] niuu:font-mono niuu:text-text-faint">
                  {(selectedMount.categories ?? []).join(', ')}
                </div>
              )}
              {(selectedMount.path || selectedMount.url) && (
                <div className="niuu:text-[10px] niuu:font-mono niuu:text-text-faint">
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

      <div className="niuu:flex niuu:flex-col niuu:gap-2">
        <div className="niuu:flex niuu:items-center niuu:justify-between">
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
          <div className="niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-elevated niuu:p-3 niuu:text-xs niuu:text-text-muted">
            No workflow bindings yet. The resource exists in the graph, but nothing is attached to
            it.
          </div>
        ) : (
          bindings.map((binding) => (
            <div
              key={binding.id}
              className="niuu:rounded-xl niuu:border niuu:border-border-subtle niuu:bg-bg-elevated niuu:p-3.5 niuu:flex niuu:flex-col niuu:gap-3"
            >
              <div className="niuu:flex niuu:items-center niuu:justify-between">
                <span className="niuu:text-[11px] niuu:font-semibold niuu:text-text-primary">
                  {binding.targetType} binding
                </span>
                <button
                  type="button"
                  className="niuu:bg-transparent niuu:border-none niuu:text-text-faint niuu:cursor-pointer"
                  onClick={() => onRemoveResourceBinding(binding.id)}
                >
                  ×
                </button>
              </div>

              <div className="niuu:grid niuu:grid-cols-2 niuu:gap-2">
                <div>
                  <label className={SECTION_LABEL}>Target type</label>
                  <select
                    className={cn(INPUT, 'niuu:mt-0.5')}
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
                    className={cn(INPUT, 'niuu:mt-0.5')}
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
                    className={cn(INPUT, 'niuu:mt-0.5')}
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
                    className={cn(INPUT, 'niuu:mt-0.5')}
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

              <div className="niuu:grid niuu:grid-cols-2 niuu:gap-2">
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

      <div className="niuu:border-t niuu:border-border niuu:pt-4">
        <button type="button" className={DELETE_BTN} onClick={() => onDeleteNode(node.id)}>
          Delete node
        </button>
      </div>
    </div>
  );
}

export function WorkflowDetailPanel({
  workflow,
  readOnly,
  selectedNode,
  errorCount,
  warnCount,
  issues,
  personas,
  models,
  registryMounts,
  onDeleteNode,
  onUpdateNode,
  onSelectSubworkflowTemplate,
  onAddSubworkflowTemplate,
  onRenameSubworkflowTemplate,
  onRemoveSubworkflowTemplate,
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
  workflows = [],
  onOpenWorkflow,
  onClose,
}: WorkflowDetailPanelProps) {
  const title = selectedNode ? selectedNode.label : 'Workflow';
  const subtitle = selectedNode
    ? `${selectedNode.kind} · ${selectedNode.id}`
    : 'Inspector and release summary';
  // The header's "open child workflow" shortcut only makes sense when the
  // node offers exactly one template — with several, which one it would
  // navigate to is ambiguous, so the shortcut is omitted (each template's
  // row still offers its own child-workflow picker in the inspector below).
  const soleTemplateAlias =
    selectedNode?.kind === 'subworkflow' && Object.keys(selectedNode.templates ?? {}).length === 1
      ? Object.values(selectedNode.templates ?? {})[0]!
      : null;
  const { catalogChildWorkflow, childWorkflowExact } =
    soleTemplateAlias !== null
      ? resolveTemplateCatalogChild(workflow, workflows, soleTemplateAlias)
      : { catalogChildWorkflow: null, childWorkflowExact: false };

  return (
    <div
      data-testid="workflow-detail-panel"
      className="niuu:absolute niuu:inset-y-3 niuu:right-3 niuu:z-10 niuu:flex niuu:w-[360px] niuu:flex-col niuu:overflow-hidden niuu:rounded-xl niuu:border niuu:border-border niuu:bg-bg-secondary/95 niuu:shadow-2xl niuu:backdrop-blur-md"
    >
      <div className="niuu:flex niuu:items-start niuu:gap-2 niuu:border-b niuu:border-border niuu:px-4 niuu:pb-2 niuu:pt-3">
        <div className="niuu:flex niuu:min-w-0 niuu:flex-col niuu:gap-0.5">
          <span className="niuu:truncate niuu:text-[13px] niuu:font-semibold niuu:text-text-primary niuu:font-sans">
            {title}
          </span>
          <span className="niuu:text-[9px] niuu:font-mono niuu:text-text-faint">{subtitle}</span>
        </div>
        {catalogChildWorkflow && onOpenWorkflow ? (
          <button
            type="button"
            data-testid="open-child-workflow"
            className="niuu:ml-auto niuu:rounded-md niuu:border niuu:border-border niuu:bg-bg-elevated niuu:px-2 niuu:py-1 niuu:text-[10px] niuu:text-text-secondary niuu:hover:border-brand niuu:hover:text-text-primary"
            onClick={() => onOpenWorkflow(catalogChildWorkflow)}
          >
            {childWorkflowExact ? 'Open pinned child' : 'Open latest child'}
          </button>
        ) : null}
        {onClose && (
          <button
            type="button"
            data-testid="workflow-detail-panel-close"
            onClick={onClose}
            aria-label="Close panel"
            title="Close panel"
            className="niuu:ml-auto niuu:rounded-md niuu:border-none niuu:bg-transparent niuu:p-1 niuu:text-[15px] niuu:leading-none niuu:text-text-muted niuu:hover:bg-bg-tertiary niuu:hover:text-text-primary"
          >
            ×
          </button>
        )}
      </div>

      <fieldset
        disabled={readOnly ?? workflow.readOnly === true}
        className="niuu:m-0 niuu:flex-1 niuu:overflow-y-auto niuu:border-0 niuu:p-0 disabled:niuu:opacity-80"
      >
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
          <div className="niuu:px-4 niuu:py-3 niuu:flex niuu:flex-col niuu:gap-4">
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
                className={cn(INPUT, 'niuu:min-h-[84px]')}
                value={selectedNode.condition}
                onChange={(e) => onUpdateNode(selectedNode.id, { condition: e.target.value })}
              />
            </div>
            <div>
              <label className={SECTION_LABEL}>Gate mode</label>
              <select
                className={cn(INPUT, 'niuu:mt-0.5')}
                aria-label="Gate mode"
                value={selectedNode.mode ?? 'human_approval'}
                onChange={(e) => {
                  const mode = e.target.value as WorkflowGateMode;
                  onUpdateNode(
                    selectedNode.id,
                    mode === 'evidence'
                      ? {
                          mode,
                          evidencePolicy: selectedNode.evidencePolicy ?? emptyEvidencePolicy(),
                          artifact: selectedNode.artifact ?? { kind: 'document', id: '' },
                        }
                      : { mode, evidencePolicy: undefined, artifact: undefined },
                  );
                }}
              >
                <option value="human_approval">human approval</option>
                <option value="human_review">human review</option>
                <option value="automated_approval">automated approval</option>
                <option value="evidence">verified evidence</option>
              </select>
            </div>
            {selectedNode.mode === 'evidence' ? (
              <>
                <div className="niuu:grid niuu:grid-cols-2 niuu:gap-2">
                  <div>
                    <label className={SECTION_LABEL}>Artifact kind</label>
                    <input
                      className={INPUT}
                      aria-label="Artifact kind"
                      value={selectedNode.artifact?.kind ?? 'document'}
                      onChange={(e) =>
                        onUpdateNode(selectedNode.id, {
                          artifact: {
                            kind: e.target.value.trimStart(),
                            id: selectedNode.artifact?.id ?? '',
                          },
                        })
                      }
                    />
                  </div>
                  <div>
                    <label className={SECTION_LABEL}>Artifact path or identifier</label>
                    <input
                      className={INPUT}
                      aria-label="Artifact path or identifier"
                      value={selectedNode.artifact?.id ?? ''}
                      placeholder="report.md"
                      onChange={(e) =>
                        onUpdateNode(selectedNode.id, {
                          artifact: {
                            kind: selectedNode.artifact?.kind ?? 'document',
                            id: e.target.value.trimStart(),
                          },
                        })
                      }
                    />
                  </div>
                </div>
                <EvidencePolicyEditor
                  policy={selectedNode.evidencePolicy}
                  onChange={(evidencePolicy) => onUpdateNode(selectedNode.id, { evidencePolicy })}
                />
              </>
            ) : (
              <div>
                <label className={SECTION_LABEL}>Pending behavior</label>
                <select
                  className={cn(INPUT, 'niuu:mt-0.5')}
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
            )}
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
                className={cn(INPUT, 'niuu:min-h-[72px]')}
                value={selectedNode.instructions ?? ''}
                onChange={(e) => onUpdateNode(selectedNode.id, { instructions: e.target.value })}
              />
            </div>
            {selectedNode.mode !== 'evidence' ? (
              <div>
                <label className={SECTION_LABEL}>Auto-forward after</label>
                <input
                  className={INPUT}
                  value={selectedNode.autoForwardAfter ?? '30m'}
                  onChange={(e) =>
                    onUpdateNode(selectedNode.id, { autoForwardAfter: e.target.value })
                  }
                />
              </div>
            ) : null}
          </div>
        ) : selectedNode?.kind === 'cond' ? (
          <div className="niuu:px-4 niuu:py-3 niuu:flex niuu:flex-col niuu:gap-4">
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
                className={cn(INPUT, 'niuu:min-h-[120px] niuu:font-mono')}
                value={selectedNode.predicate}
                onChange={(e) => onUpdateNode(selectedNode.id, { predicate: e.target.value })}
              />
            </div>
          </div>
        ) : selectedNode?.kind === 'trigger' ? (
          <div className="niuu:px-4 niuu:py-3 niuu:flex niuu:flex-col niuu:gap-4">
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
                className={cn(INPUT, 'niuu:font-mono')}
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
                className={cn(INPUT, 'niuu:font-mono')}
                value={selectedNode.source ?? 'manual dispatch'}
                onChange={(e) => onUpdateNode(selectedNode.id, { source: e.target.value })}
              />
            </div>
          </div>
        ) : selectedNode?.kind === 'end' ? (
          <div className="niuu:px-4 niuu:py-3 niuu:flex niuu:flex-col niuu:gap-4">
            <div>
              <label className={SECTION_LABEL}>End label</label>
              <input
                className={INPUT}
                value={selectedNode.label}
                onChange={(e) => onUpdateLabel(selectedNode.id, e.target.value)}
              />
            </div>
            <div className="niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-elevated niuu:p-3 niuu:text-xs niuu:text-text-secondary">
              Terminal node. Use this to make completion paths explicit in the graph and pipeline
              views.
            </div>
          </div>
        ) : selectedNode?.kind === 'wait' ? (
          <div className="niuu:px-4 niuu:py-3 niuu:flex niuu:flex-col niuu:gap-4">
            <div>
              <label className={SECTION_LABEL}>Wait label</label>
              <input
                className={INPUT}
                value={selectedNode.label}
                onChange={(e) => onUpdateLabel(selectedNode.id, e.target.value)}
              />
            </div>
            <div className="niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-elevated niuu:p-3 niuu:text-xs niuu:text-text-secondary">
              Passive external wait. Its incoming and continuation event types are carried by the
              connected edges.
              <div className="niuu:mt-3 niuu:grid niuu:grid-cols-2 niuu:gap-2 niuu:text-[10px] niuu:font-mono">
                <div>
                  <div className={SECTION_LABEL}>Incoming</div>
                  {workflow.edges
                    .filter((edge) => edge.target === selectedNode.id)
                    .map((edge) => parseWorkflowEdgeLabel(edge.label)?.targetEventType)
                    .filter(Boolean)
                    .join(', ') || 'Connect and configure'}
                </div>
                <div>
                  <div className={SECTION_LABEL}>Continuation</div>
                  {workflow.edges
                    .filter((edge) => edge.source === selectedNode.id)
                    .map((edge) => parseWorkflowEdgeLabel(edge.label)?.sourceEventType)
                    .filter(Boolean)
                    .join(', ') || 'Connect and configure'}
                </div>
              </div>
            </div>
          </div>
        ) : selectedNode?.kind === 'subworkflow' ? (
          <SubworkflowInspector
            node={selectedNode}
            workflow={workflow}
            workflows={workflows}
            personas={personas}
            onSelectSubworkflowTemplate={onSelectSubworkflowTemplate}
            onAddSubworkflowTemplate={onAddSubworkflowTemplate}
            onRenameSubworkflowTemplate={onRenameSubworkflowTemplate}
            onRemoveSubworkflowTemplate={onRemoveSubworkflowTemplate}
            onUpdateNode={onUpdateNode}
            onUpdateLabel={onUpdateLabel}
            onDeleteNode={onDeleteNode}
          />
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
      </fieldset>
    </div>
  );
}
