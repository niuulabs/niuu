/**
 * WorkflowBuilder — contextual DAG editor for Ting workflows.
 *
 * Views:
 *  • Graph   — pan/zoom SVG canvas with node/edge editing
 *  • Pipeline — read-only topological layout
 *  • YAML    — read-only pretty-printed YAML
 *
 * Owner: plugin-ting (WorkflowBuilder).
 */

import { useEffect, useMemo, useState } from 'react';
import type { Workflow } from '../../domain/workflow';
import type { WorkflowExportFormat, WorkflowVersionSummary } from '../../ports';
import { validateWorkflowFull } from '../../domain/workflowValidation';
import {
  useWorkflowBuilder,
  type WorkflowStageModelOption,
  type WorkflowView,
} from './useWorkflowBuilder';
import { GraphView } from './GraphView';
import { PipelineView } from './PipelineView';
import { YamlView } from './YamlView';
import { ValidationPanel } from './ValidationPanel';
import { DEFAULT_PERSONAS, type PersonaEntry } from './LibraryPanel';
import { WorkflowDetailPanel } from './WorkflowDetailPanel';
import type { WorkflowRegistryMount } from './mimirRegistry';

export interface WorkflowBuilderProps {
  /** Initial workflow to edit. */
  initialWorkflow: Workflow;
  /** Persisted baseline when `initialWorkflow` is a restored unsaved draft. */
  initialSavedWorkflow?: Workflow;
  /** Called whenever the workflow is mutated. */
  onSave?: (workflow: Workflow) => Workflow | void | Promise<Workflow | void>;
  onSaved?: (workflow: Workflow) => void;
  savePending?: boolean;
  mode?: WorkflowEditorMode;
  onEdit?: (workflow: Workflow) => void;
  onCancelEdit?: () => void;
  versions?: WorkflowVersionSummary[];
  onSelectVersion?: (version: string) => void;
  versionPending?: boolean;
  /** Override persona library (defaults to DEFAULT_PERSONAS). */
  personas?: PersonaEntry[];
  /** Models available for explicit per-stage assignment. */
  models?: WorkflowStageModelOption[];
  /** Registry-backed Mimir mounts available for drag/drop in the workflow editor. */
  registryMounts?: WorkflowRegistryMount[];
  /** Called when the user wants to launch the current workflow. */
  onLaunch?: (workflow: Workflow) => void;
  /** Optional loading flag for workflow launch actions. */
  launchPending?: boolean;
  /** Export the persisted portable document or self-contained bundle. */
  onExport?: (format: WorkflowExportFormat) => void;
  exportPending?: boolean;
  /** Explicitly repin one dependency to the current local persona revision. */
  onRefreshPersona?: (workflow: Workflow, alias: string) => void;
  /** Catalog actions stay optional so the editor remains independently embeddable. */
  workflowCatalog?: Workflow[];
  onSelectWorkflow?: (workflow: Workflow) => void;
  onCreateWorkflow?: () => void;
  onImportWorkflow?: () => void;
  onDeleteWorkflow?: (workflow: Workflow) => void;
  /** Exact parent locations for child-workflow breadcrumb navigation. */
  ancestry?: readonly WorkflowEditorLocation[];
  /** Opens a child while preserving the current editor location as its parent. */
  onOpenWorkflow?: (workflow: Workflow, current: WorkflowEditorLocation) => void;
  /** Navigates to one ancestor by zero-based index. */
  onNavigateAncestor?: (index: number, current: WorkflowEditorLocation) => void;
}

export type WorkflowEditorMode = 'view' | 'edit';

export interface WorkflowEditorLocation {
  workflow: Workflow;
  savedWorkflow: Workflow;
  mode: WorkflowEditorMode;
}

const VIEWS: WorkflowView[] = ['graph', 'pipeline', 'yaml'];

const VIEW_LABELS: Record<WorkflowView, string> = {
  graph: 'Graph',
  pipeline: 'Pipeline',
  yaml: 'YAML',
};
const ACTION_BTN =
  'niuu:bg-transparent niuu:border niuu:border-solid niuu:border-border-subtle niuu:rounded-md niuu:text-text-secondary niuu:font-sans niuu:text-xs niuu:py-1.5 niuu:px-3 niuu:cursor-pointer niuu:whitespace-nowrap niuu:hover:border-border niuu:hover:text-text-primary niuu:transition-colors';

export function WorkflowBuilder({
  initialWorkflow,
  initialSavedWorkflow = initialWorkflow,
  onSave,
  onSaved,
  savePending = false,
  mode,
  onEdit,
  onCancelEdit,
  versions = [],
  onSelectVersion,
  versionPending = false,
  personas,
  models = [],
  registryMounts = [],
  onLaunch,
  launchPending = false,
  onExport,
  exportPending = false,
  onRefreshPersona,
  workflowCatalog = [],
  onSelectWorkflow,
  onCreateWorkflow,
  onImportWorkflow,
  onDeleteWorkflow,
  ancestry = [],
  onOpenWorkflow,
  onNavigateAncestor,
}: WorkflowBuilderProps) {
  const builder = useWorkflowBuilder(
    initialWorkflow,
    personas ?? DEFAULT_PERSONAS,
    models,
    initialSavedWorkflow,
  );
  const {
    workflow,
    savedWorkflow,
    isDirty,
    view,
    selectedNodeId,
    connectingFromId,
    connectingFromLabel,
    setView,
    selectNode,
    inspectNode,
    addNode,
    addMimirResource,
    addStageWithPersona,
    addStageFromPort,
    addNodeFromPort,
    deleteNode,
    deleteEdge,
    moveNode,
    autoLayout,
    undo,
    redo,
    canUndo,
    canRedo,
    markSaved,
    discardChanges,
    startConnect,
    cancelConnect,
    completeConnect,
    addPersonaToStage,
    replacePersonaInStage,
    updatePersonaBudget,
    updatePersonaModel,
    removePersonaFromStage,
    updateNodeLabel,
    updateNode,
    selectSubworkflowTemplate,
    addSubworkflowTemplate,
    renameSubworkflowTemplate,
    removeSubworkflowTemplate,
    addResourceBinding,
    updateResourceBinding,
    removeResourceBinding,
    updateWorkflowMeta,
    setWorkflow: _setWorkflow,
  } = builder;

  const modelCatalog = useMemo(
    () =>
      Object.fromEntries(models.map((model) => [model.id, { vendor: model.vendor }])) as Record<
        string,
        { vendor?: string }
      >,
    [models],
  );
  const issues = useMemo(
    () => validateWorkflowFull(workflow, modelCatalog),
    [modelCatalog, workflow],
  );
  const errorCount = issues.filter((i) => i.severity === 'error').length;
  const warnCount = issues.filter((i) => i.severity === 'warning').length;
  const unresolvedPersonas = Object.entries(workflow.personaDependencies ?? {}).filter(
    ([, dependency]) => dependency.resolved === false,
  );
  const unresolvedRequirements = (workflow.requirements ?? []).filter(
    (requirement) => !requirement.resolved,
  );
  const launchBlocked =
    isDirty || unresolvedPersonas.length > 0 || unresolvedRequirements.length > 0;
  const editing = mode ? mode === 'edit' : workflow.readOnly !== true;
  const canEdit = workflow.canEdit ?? !workflow.readOnly;
  const editorReadOnly = !editing || !canEdit || versionPending;

  const selectedNode = selectedNodeId
    ? (workflow.nodes.find((n) => n.id === selectedNodeId) ?? null)
    : null;

  // The detail panel floats: open whenever a node is selected, or when the
  // header title is clicked for the workflow-level summary (name,
  // description, version, tags). Selecting a real node always takes over —
  // even if the panel was closed a moment ago — handled at the call sites
  // below (selectNodeAndCloseMeta) rather than as an effect reacting to
  // selectedNodeId, which would be derived state via a render-triggering
  // effect.
  const [metaOpen, setMetaOpen] = useState(false);
  // Pipeline view also supports node selection (onSelectNode is wired to
  // PipelineView too), so this isn't gated on the Graph tab specifically.
  const panelOpen = selectedNode !== null || metaOpen;

  const [catalogOpen, setCatalogOpen] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const hasWorkflowCatalog = workflowCatalog.length > 0 || Boolean(onCreateWorkflow);
  useEffect(() => {
    if (!isDirty) return;
    function protectUnsavedDraft(event: BeforeUnloadEvent) {
      event.preventDefault();
      event.returnValue = '';
    }
    window.addEventListener('beforeunload', protectUnsavedDraft);
    return () => window.removeEventListener('beforeunload', protectUnsavedDraft);
  }, [isDirty]);

  async function handleSave() {
    const submitted = workflow;
    setSaveError(null);
    try {
      const saved = await onSave?.(submitted);
      if (saved) {
        const changedWhileSaving = markSaved(saved, submitted);
        if (!changedWhileSaving) onSaved?.(saved);
        return;
      }
      markSaved(submitted);
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : 'Workflow save failed.');
    }
  }

  function handleLaunch() {
    onLaunch?.(workflow);
  }

  function handleClosePanel() {
    setMetaOpen(false);
    if (selectedNodeId) selectNode(null);
  }

  /** Selecting a real node closes the workflow-meta panel, if it was open;
   *  deselecting (id === null, e.g. clicking empty canvas) leaves it alone. */
  function selectNodeAndCloseMeta(id: string | null) {
    if (id) setMetaOpen(false);
    selectNode(id);
  }

  function handleSelectWorkflow(next: Workflow) {
    if (
      next.id === workflow.id &&
      (next.documentRevision ?? next.version) === (workflow.documentRevision ?? workflow.version)
    ) {
      setCatalogOpen(false);
      return;
    }
    if (isDirty && !window.confirm('Discard unsaved changes and open another workflow?')) return;
    onSelectWorkflow?.(next);
    setCatalogOpen(false);
  }

  function currentLocation(): WorkflowEditorLocation {
    return {
      workflow,
      savedWorkflow,
      mode: editing ? 'edit' : 'view',
    };
  }

  function handleOpenChild(next: Workflow) {
    onOpenWorkflow?.(next, currentLocation());
  }

  function handleNavigateAncestor(index: number) {
    if (isDirty && !window.confirm('Discard unsaved changes and return to the parent workflow?')) {
      return;
    }
    onNavigateAncestor?.(index, currentLocation());
  }

  function confirmDiscardDraft(): boolean {
    return !isDirty || window.confirm('Discard unsaved changes?');
  }

  return (
    <div
      data-testid="workflow-builder"
      className="niuu:flex niuu:h-full niuu:min-h-0 niuu:font-sans"
    >
      {/* Center: one action header + canvas */}
      <div className="niuu:flex-1 niuu:flex niuu:flex-col niuu:min-w-0">
        <header
          data-testid="workflow-editor-header"
          className="niuu:relative niuu:flex niuu:min-h-16 niuu:flex-wrap niuu:items-center niuu:gap-x-5 niuu:gap-y-3 niuu:border-b niuu:border-border niuu:bg-bg-secondary niuu:px-4 niuu:py-2 niuu:shrink-0"
        >
          <div className="niuu:flex niuu:min-w-0 niuu:flex-1 niuu:items-center niuu:gap-2">
            {ancestry.length > 0 && onNavigateAncestor ? (
              <button
                type="button"
                data-testid="workflow-parent-back"
                aria-label={`Back to ${ancestry.at(-1)?.workflow.name ?? 'parent workflow'}`}
                disabled={versionPending}
                onClick={() => handleNavigateAncestor(ancestry.length - 1)}
                className="niuu:rounded-md niuu:border niuu:border-transparent niuu:bg-transparent niuu:px-1.5 niuu:py-1 niuu:text-sm niuu:text-text-muted niuu:hover:border-border niuu:hover:text-text-primary niuu:disabled:opacity-40"
              >
                ←
              </button>
            ) : null}
            <nav
              aria-label="Workflow path"
              className="niuu:flex niuu:min-w-0 niuu:items-center niuu:gap-1"
            >
              {ancestry.map((entry, index) => (
                <span
                  key={`${entry.workflow.id}:${entry.workflow.documentRevision ?? entry.workflow.version ?? index}`}
                  className="niuu:flex niuu:min-w-0 niuu:items-center niuu:gap-1"
                >
                  <button
                    type="button"
                    data-testid={`workflow-parent-${index}`}
                    disabled={versionPending || !onNavigateAncestor}
                    onClick={() => handleNavigateAncestor(index)}
                    className="niuu:max-w-36 niuu:truncate niuu:border-0 niuu:bg-transparent niuu:p-1 niuu:text-[11px] niuu:text-text-muted niuu:hover:text-text-primary niuu:disabled:opacity-40"
                  >
                    {entry.workflow.name}
                  </button>
                  <span aria-hidden="true" className="niuu:text-text-faint">
                    /
                  </span>
                </span>
              ))}
              <button
                type="button"
                data-testid="workflow-picker-trigger"
                disabled={versionPending}
                aria-expanded={hasWorkflowCatalog ? catalogOpen : undefined}
                onClick={() => {
                  if (hasWorkflowCatalog) {
                    setCatalogOpen((open) => !open);
                    return;
                  }
                  selectNode(null);
                  setMetaOpen((open) => !open);
                }}
                className="niuu:flex niuu:min-w-0 niuu:items-center niuu:gap-2 niuu:rounded-md niuu:border niuu:border-transparent niuu:bg-transparent niuu:px-2 niuu:py-1 niuu:text-left niuu:cursor-pointer niuu:hover:border-border niuu:hover:bg-bg-tertiary"
              >
                {ancestry.length === 0 ? (
                  <span className="niuu:shrink-0 niuu:whitespace-nowrap niuu:text-[10px] niuu:font-mono niuu:uppercase niuu:tracking-wider niuu:text-text-faint">
                    Workflows /
                  </span>
                ) : null}
                <span
                  className="niuu:truncate niuu:text-sm niuu:font-semibold niuu:text-text-primary"
                  data-testid="builder-title"
                >
                  {workflow.name}
                </span>
                {hasWorkflowCatalog ? (
                  <span aria-hidden="true" className="niuu:text-[10px] niuu:text-text-muted">
                    ▾
                  </span>
                ) : null}
              </button>
            </nav>
          </div>

          {isDirty && editing ? (
            <span
              data-testid="workflow-dirty-state"
              className="niuu:inline-flex niuu:items-center niuu:gap-1.5 niuu:text-[10px] niuu:font-mono niuu:text-status-amber"
            >
              <span className="niuu:h-1.5 niuu:w-1.5 niuu:rounded-full niuu:bg-status-amber" />
              Unsaved
            </span>
          ) : null}

          {workflow.version &&
            (onSelectVersion && versions.length > 0 ? (
              <select
                aria-label="Workflow version"
                data-testid="workflow-version-picker"
                value={workflow.version}
                disabled={versionPending}
                onChange={(event) => {
                  if (!confirmDiscardDraft()) return;
                  onSelectVersion(event.target.value);
                }}
                className="niuu:rounded niuu:border niuu:border-border niuu:bg-bg-elevated niuu:px-1.5 niuu:py-0.5 niuu:text-[10px] niuu:font-mono niuu:text-text-muted"
              >
                {versions.map((item) => (
                  <option key={item.documentRevision} value={item.version}>
                    v{item.version}
                    {item.isHead ? ' · latest' : ''}
                  </option>
                ))}
              </select>
            ) : (
              <span
                className="niuu:text-[10px] niuu:font-mono niuu:text-text-muted niuu:bg-bg-elevated niuu:border niuu:border-border niuu:rounded niuu:px-1.5 niuu:py-0.5"
                data-testid="builder-version"
              >
                v{workflow.version}
              </span>
            ))}
          {versionPending ? (
            <span
              role="status"
              data-testid="workflow-versions-loading"
              className="niuu:text-[10px] niuu:text-text-muted"
            >
              Loading versions…
            </span>
          ) : null}

          <span
            data-testid="workflow-editor-mode"
            className="niuu:text-[10px] niuu:font-mono niuu:uppercase niuu:tracking-wide niuu:text-text-muted"
          >
            {editing ? 'Editing' : 'Viewing'}
          </span>

          <label className="niuu:flex niuu:items-center">
            <span className="niuu:sr-only">Visualization</span>
            <select
              data-testid="workflow-visualization"
              aria-label="Visualization"
              value={view}
              onChange={(event) => setView(event.target.value as WorkflowView)}
              className="niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-elevated niuu:px-2 niuu:py-1.5 niuu:text-xs niuu:text-text-secondary"
            >
              {VIEWS.map((option) => (
                <option key={option} value={option}>
                  {VIEW_LABELS[option]}
                </option>
              ))}
            </select>
          </label>

          <div className="niuu:flex niuu:shrink-0 niuu:items-center niuu:gap-3">
            {editing ? (
              <div className="niuu:flex niuu:items-center niuu:gap-1">
                <button
                  type="button"
                  data-testid="btn-undo"
                  aria-label="Undo"
                  title="Undo"
                  onClick={undo}
                  disabled={!canUndo || versionPending}
                  className={`${ACTION_BTN} niuu:px-2`}
                >
                  ↶
                </button>
                <button
                  type="button"
                  data-testid="btn-redo"
                  aria-label="Redo"
                  title="Redo"
                  onClick={redo}
                  disabled={!canRedo || versionPending}
                  className={`${ACTION_BTN} niuu:px-2`}
                >
                  ↷
                </button>
              </div>
            ) : null}
            <details className="niuu:relative" data-testid="workflow-more">
              <summary className={ACTION_BTN}>More</summary>
              <div className="niuu:absolute niuu:right-0 niuu:top-full niuu:z-30 niuu:mt-2 niuu:flex niuu:min-w-[220px] niuu:flex-col niuu:gap-1 niuu:rounded-lg niuu:border niuu:border-border niuu:bg-bg-elevated niuu:p-2 niuu:shadow-2xl">
                <button
                  type="button"
                  data-testid="open-workflow-meta"
                  onClick={() => {
                    selectNode(null);
                    setMetaOpen((open) => !open);
                  }}
                  className={ACTION_BTN}
                >
                  Workflow details
                </button>
                {workflow.origin === 'bundled' ||
                unresolvedPersonas.length > 0 ||
                unresolvedRequirements.length > 0 ? (
                  <details data-testid="workflow-catalog-status">
                    <summary className="niuu:cursor-pointer niuu:px-2 niuu:py-1 niuu:text-xs niuu:text-status-amber">
                      {workflow.origin === 'bundled'
                        ? 'Bundled workflow'
                        : `${unresolvedPersonas.length + unresolvedRequirements.length} unresolved`}
                    </summary>
                    <div className="niuu:space-y-2 niuu:border-t niuu:border-border-subtle niuu:p-2 niuu:text-xs niuu:text-text-secondary">
                      {workflow.origin === 'bundled' ? (
                        <div>
                          Bundled workflow identity. Existing versions are immutable
                          {canEdit ? '; Edit creates a new version.' : '.'}
                        </div>
                      ) : null}
                      {unresolvedPersonas.map(([alias, dependency]) => (
                        <div key={alias} data-testid={`unresolved-persona-${alias}`}>
                          Persona {alias}: {dependency.id}@{dependency.revision} is unresolved
                          {dependency.message ? ` — ${dependency.message}` : ''}.
                        </div>
                      ))}
                      {unresolvedRequirements.map((requirement) => (
                        <div
                          key={requirement.id}
                          data-testid={`unresolved-requirement-${requirement.id}`}
                        >
                          {requirement.message ||
                            `${requirement.kind} binding ${requirement.id} is unresolved.`}
                        </div>
                      ))}
                      {Object.entries(workflow.personaDependencies ?? {})
                        .filter(([, dependency]) => dependency.resolved !== false)
                        .map(([alias, dependency]) => (
                          <div key={alias} data-testid={`persona-dependency-${alias}`}>
                            Persona {alias}: {dependency.id}@{dependency.revision}{' '}
                            {onRefreshPersona && editing && !versionPending ? (
                              <button
                                type="button"
                                className="niuu:border-0 niuu:bg-transparent niuu:p-0 niuu:text-xs niuu:text-brand niuu:cursor-pointer niuu:hover:underline"
                                title="Updates this dependency for future runs; existing run snapshots do not change."
                                onClick={() => onRefreshPersona(workflow, alias)}
                              >
                                Use current local persona
                              </button>
                            ) : null}
                          </div>
                        ))}
                    </div>
                  </details>
                ) : null}
                {onImportWorkflow ? (
                  <button
                    type="button"
                    className={ACTION_BTN}
                    onClick={() => {
                      if (confirmDiscardDraft()) onImportWorkflow();
                    }}
                    disabled={versionPending}
                  >
                    Import workflow
                  </button>
                ) : null}
                {onExport ? (
                  <>
                    <button
                      type="button"
                      className={ACTION_BTN}
                      data-testid="export-workflow-yaml"
                      disabled={exportPending || versionPending}
                      onClick={() => onExport('yaml')}
                    >
                      Export YAML
                    </button>
                    <button
                      type="button"
                      className={ACTION_BTN}
                      data-testid="export-workflow-bundle"
                      disabled={exportPending || versionPending}
                      onClick={() => onExport('bundle')}
                    >
                      Export bundle
                    </button>
                  </>
                ) : null}
                {onDeleteWorkflow && canEdit && workflow.origin !== 'bundled' ? (
                  <button
                    type="button"
                    data-testid={`delete-workflow-${workflow.id}`}
                    className={ACTION_BTN}
                    onClick={() => onDeleteWorkflow(workflow)}
                    disabled={versionPending}
                  >
                    Delete workflow
                  </button>
                ) : null}
              </div>
            </details>
            {editing && onSave && (
              <button
                type="button"
                className={ACTION_BTN}
                data-testid="save-workflow"
                onClick={handleSave}
                disabled={savePending || versionPending || !isDirty}
              >
                {savePending ? 'Saving…' : 'Save'}
              </button>
            )}
            {editing && onCancelEdit ? (
              <button
                type="button"
                className={ACTION_BTN}
                data-testid="cancel-workflow-edit"
                onClick={() => {
                  if (confirmDiscardDraft()) {
                    discardChanges();
                    onCancelEdit();
                  }
                }}
                disabled={versionPending}
              >
                Cancel
              </button>
            ) : null}
            {!editing && canEdit && onEdit ? (
              <button
                type="button"
                className={ACTION_BTN}
                data-testid="edit-workflow"
                onClick={() => onEdit(workflow)}
                disabled={versionPending}
              >
                Edit
              </button>
            ) : null}
            {onLaunch && (
              <button
                type="button"
                className="niuu:rounded-md niuu:border niuu:border-brand niuu:bg-brand niuu:px-3 niuu:py-1.5 niuu:text-xs niuu:font-semibold niuu:text-bg-primary niuu:cursor-pointer niuu:hover:opacity-90 niuu:disabled:cursor-not-allowed niuu:disabled:opacity-40"
                data-testid="launch-workflow"
                onClick={handleLaunch}
                disabled={launchPending || versionPending || launchBlocked}
                title={
                  isDirty
                    ? 'Save changes before launching.'
                    : launchBlocked
                      ? 'Resolve persona dependencies and local bindings first.'
                      : undefined
                }
              >
                {launchPending ? 'Launching…' : 'Launch…'}
              </button>
            )}
          </div>

          {catalogOpen && hasWorkflowCatalog ? (
            <section
              aria-label="Workflow picker"
              data-testid="workflow-picker"
              className="niuu:absolute niuu:left-4 niuu:top-full niuu:z-40 niuu:mt-2 niuu:w-[360px] niuu:max-w-[calc(100vw-2rem)] niuu:rounded-xl niuu:border niuu:border-border niuu:bg-bg-elevated niuu:p-2 niuu:shadow-2xl"
            >
              <div className="niuu:flex niuu:items-center niuu:justify-between niuu:px-2 niuu:pb-2">
                <span className="niuu:text-[10px] niuu:font-mono niuu:uppercase niuu:tracking-wider niuu:text-text-muted">
                  Saved workflows
                </span>
                {onCreateWorkflow ? (
                  <button
                    type="button"
                    data-testid="new-workflow"
                    className={ACTION_BTN}
                    onClick={() => {
                      if (confirmDiscardDraft()) onCreateWorkflow();
                    }}
                  >
                    New workflow
                  </button>
                ) : null}
              </div>
              <div className="niuu:max-h-[360px] niuu:overflow-y-auto">
                {workflowCatalog.map((item) => (
                  <button
                    type="button"
                    key={item.id}
                    data-testid={`workflow-tab-${item.id}`}
                    aria-current={item.id === workflow.id ? 'page' : undefined}
                    onClick={() => handleSelectWorkflow(item)}
                    className="niuu:flex niuu:w-full niuu:items-center niuu:gap-3 niuu:rounded-lg niuu:border niuu:border-transparent niuu:bg-transparent niuu:px-3 niuu:py-2.5 niuu:text-left niuu:text-text-secondary niuu:hover:border-border niuu:hover:bg-bg-tertiary aria-[current=page]:niuu:bg-bg-tertiary aria-[current=page]:niuu:text-text-primary"
                  >
                    <span className="niuu:text-brand">◇</span>
                    <span className="niuu:min-w-0 niuu:flex-1">
                      <span className="niuu:block niuu:truncate niuu:text-xs niuu:font-semibold">
                        {item.name}
                      </span>
                      <span className="niuu:block niuu:text-[9px] niuu:font-mono niuu:text-text-faint">
                        {item.nodes.length} steps · {item.edges.length} connections
                        {item.readOnly ? ' · system' : ''}
                      </span>
                    </span>
                  </button>
                ))}
              </div>
            </section>
          ) : null}
        </header>

        {saveError ? (
          <div
            role="alert"
            className="niuu:border-b niuu:border-critical niuu:bg-critical-bg niuu:px-4 niuu:py-2 niuu:text-xs niuu:text-critical"
          >
            {saveError}
          </div>
        ) : null}

        {/* Canvas content */}
        <div className="niuu:flex-1 niuu:flex niuu:flex-col niuu:relative niuu:min-h-0">
          {view === 'graph' && (
            <GraphView
              readOnly={editorReadOnly}
              readOnlyMessage={versionPending ? 'Loading the selected version…' : undefined}
              onEdit={!versionPending && canEdit && onEdit ? () => onEdit(workflow) : undefined}
              nodes={workflow.nodes}
              edges={workflow.edges}
              selectedNodeId={selectedNodeId}
              connectingFromId={connectingFromId}
              connectingFromLabel={connectingFromLabel}
              onSelectNode={selectNodeAndCloseMeta}
              onInspectNode={inspectNode}
              onAddNode={addNode}
              onAddMimirResource={addMimirResource}
              registryMounts={registryMounts}
              onDeleteNode={deleteNode}
              onDeleteEdge={deleteEdge}
              onMoveNode={moveNode}
              onStartConnect={startConnect}
              onCancelConnect={cancelConnect}
              onCompleteConnect={completeConnect}
              onAddPersonaToStage={addPersonaToStage}
              onAddStageWithPersona={addStageWithPersona}
              onAddStageFromPort={addStageFromPort}
              onAddNodeFromPort={addNodeFromPort}
              onAutoLayout={autoLayout}
              personas={personas ?? DEFAULT_PERSONAS}
              issues={issues}
            />
          )}
          {view === 'pipeline' && (
            <PipelineView
              nodes={workflow.nodes}
              edges={workflow.edges}
              selectedNodeId={selectedNodeId}
              onSelectNode={selectNodeAndCloseMeta}
              models={models}
            />
          )}
          {view === 'yaml' && <YamlView workflow={workflow} />}

          {/* ValidationPanel overlays the canvas */}
          <ValidationPanel
            workflow={workflow}
            onSelectNode={selectNodeAndCloseMeta}
            errorCount={errorCount}
            warnCount={warnCount}
          />

          {/* Detail panel — floats over the canvas; open on selection or
              via the header title, closed otherwise (docs/mockups/workflow-builder) */}
          {panelOpen && (
            <WorkflowDetailPanel
              workflow={workflow}
              readOnly={editorReadOnly}
              selectedNode={selectedNode}
              errorCount={errorCount}
              warnCount={warnCount}
              issues={issues}
              personas={personas ?? DEFAULT_PERSONAS}
              models={models}
              registryMounts={registryMounts}
              onDeleteNode={deleteNode}
              onUpdateNode={updateNode}
              onSelectSubworkflowTemplate={selectSubworkflowTemplate}
              onAddSubworkflowTemplate={addSubworkflowTemplate}
              onRenameSubworkflowTemplate={renameSubworkflowTemplate}
              onRemoveSubworkflowTemplate={removeSubworkflowTemplate}
              onUpdateLabel={updateNodeLabel}
              onUpdateWorkflowMeta={updateWorkflowMeta}
              onAddPersona={addPersonaToStage}
              onReplacePersona={replacePersonaInStage}
              onUpdatePersonaModel={updatePersonaModel}
              onUpdatePersonaBudget={updatePersonaBudget}
              onRemovePersona={removePersonaFromStage}
              onAddResourceBinding={addResourceBinding}
              onUpdateResourceBinding={updateResourceBinding}
              onRemoveResourceBinding={removeResourceBinding}
              workflows={workflowCatalog}
              onOpenWorkflow={onOpenWorkflow ? handleOpenChild : undefined}
              onClose={handleClosePanel}
            />
          )}
        </div>
      </div>
    </div>
  );
}
