/**
 * WorkflowBuilder — three-view DAG editor for Ting Workflows.
 *
 * Layout matches web2 prototype:
 *   LEFT:   LibraryPanel (blocks + personas)
 *   CENTER: header bar + action bar + canvas + bottom bar with zoom controls
 *   RIGHT:  WorkflowDetailPanel (name, description, version, summary)
 *
 * Views:
 *  • Graph   — pan/zoom SVG canvas with node/edge editing
 *  • Pipeline — read-only topological layout
 *  • YAML    — read-only pretty-printed YAML
 *
 * Owner: plugin-ting (WorkflowBuilder).
 */

import { useMemo } from 'react';
import { SegmentedFilter } from '@niuulabs/ui';
import type { Workflow } from '../../domain/workflow';
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
import { LibraryPanel, DEFAULT_PERSONAS, type PersonaEntry } from './LibraryPanel';
import { WorkflowDetailPanel } from './WorkflowDetailPanel';
import type { WorkflowRegistryMount } from './mimirRegistry';

export interface WorkflowBuilderProps {
  /** Initial workflow to edit. */
  initialWorkflow: Workflow;
  /** Called whenever the workflow is mutated. */
  onSave?: (workflow: Workflow) => void;
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
}

const VIEWS: WorkflowView[] = ['graph', 'pipeline', 'yaml'];

const VIEW_LABELS: Record<WorkflowView, string> = {
  graph: 'Graph',
  pipeline: 'Pipeline',
  yaml: 'YAML',
};
const VIEW_OPTIONS = VIEWS.map((value) => ({ value, label: VIEW_LABELS[value] }));

const ACTION_BTN =
  'niuu-bg-transparent niuu-border niuu-border-solid niuu-border-border-subtle niuu-rounded-md niuu-text-text-secondary niuu-font-sans niuu-text-xs niuu-py-1.5 niuu-px-3 niuu-cursor-pointer niuu-whitespace-nowrap hover:niuu-border-border hover:niuu-text-text-primary niuu-transition-colors';

export function WorkflowBuilder({
  initialWorkflow,
  onSave,
  personas,
  models = [],
  registryMounts = [],
  onLaunch,
  launchPending = false,
}: WorkflowBuilderProps) {
  const builder = useWorkflowBuilder(initialWorkflow, personas ?? DEFAULT_PERSONAS, models);
  const {
    workflow,
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
    deleteNode,
    deleteEdge,
    moveNode,
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

  const selectedNode = selectedNodeId
    ? (workflow.nodes.find((n) => n.id === selectedNodeId) ?? null)
    : null;

  function handleSave() {
    onSave?.(workflow);
  }

  function handleLaunch() {
    onLaunch?.(workflow);
  }

  return (
    <div
      data-testid="workflow-builder"
      className="niuu-flex niuu-h-full niuu-min-h-[600px] niuu-font-sans"
    >
      {/* Library panel — left of canvas */}
      {view === 'graph' && (
        <LibraryPanel personas={personas ?? DEFAULT_PERSONAS} registryMounts={registryMounts} />
      )}

      {/* Center: header + action bar + canvas + bottom bar */}
      <div className="niuu-flex-1 niuu-flex niuu-flex-col niuu-min-w-0">
        {/* Header bar */}
        <div className="niuu-flex niuu-items-center niuu-py-3 niuu-px-6 niuu-border-b niuu-border-border niuu-bg-bg-secondary niuu-shrink-0 niuu-gap-6">
          <div className="niuu-flex niuu-flex-col niuu-gap-0.5 niuu-min-w-0">
            <h3
              className="niuu-m-0 niuu-text-sm niuu-font-semibold niuu-text-text-primary niuu-whitespace-nowrap"
              data-testid="builder-title"
            >
              {workflow.name}
            </h3>
            <span className="niuu-text-[10px] niuu-font-mono niuu-text-text-faint niuu-max-w-[320px] niuu-truncate">
              {workflow.description || 'Release automation workflow'}
            </span>
          </div>

          {workflow.version && (
            <span
              className="niuu-text-[10px] niuu-font-mono niuu-text-text-muted niuu-bg-bg-elevated niuu-border niuu-border-border niuu-rounded niuu-px-1.5 niuu-py-0.5"
              data-testid="builder-version"
            >
              v{workflow.version}
            </span>
          )}

          {/* View tabs */}
          <SegmentedFilter
            options={VIEW_OPTIONS}
            value={view}
            onChange={setView}
            aria-label="Workflow view"
            className="niuu-self-center niuu-rounded-xl niuu-border niuu-border-border-subtle"
          />

          {/* Spacer + toolbar buttons */}
          <div className="niuu-flex niuu-items-center niuu-gap-4 niuu-ml-auto niuu-self-center">
            <button
              type="button"
              className="niuu-bg-transparent niuu-border-none niuu-text-text-muted niuu-cursor-pointer niuu-text-sm niuu-p-0 hover:niuu-text-text-secondary"
              title="Undo"
              aria-label="Undo"
            >
              ↺
            </button>
            <button
              type="button"
              className="niuu-bg-transparent niuu-border-none niuu-text-text-muted niuu-cursor-pointer niuu-text-sm niuu-p-0 hover:niuu-text-text-secondary"
              title="Redo"
              aria-label="Redo"
            >
              ↻
            </button>
            <div className="niuu-h-7 niuu-w-px niuu-bg-border" />
            <button type="button" className={ACTION_BTN} data-testid="btn-diff">
              Diff
            </button>
            <button type="button" className={ACTION_BTN}>
              History
            </button>
          </div>
        </div>

        {/* Action bar */}
        <div
          className="niuu-flex niuu-items-center niuu-gap-2 niuu-py-3 niuu-px-6 niuu-border-b niuu-border-border niuu-bg-bg-secondary niuu-shrink-0"
          data-testid="action-bar"
        >
          {onSave && (
            <button
              type="button"
              className={ACTION_BTN}
              data-testid="save-workflow"
              onClick={handleSave}
            >
              Save as…
            </button>
          )}
          {onLaunch && (
            <button
              type="button"
              className={ACTION_BTN}
              data-testid="launch-workflow"
              onClick={handleLaunch}
              disabled={launchPending}
            >
              {launchPending ? 'Launching…' : 'Launch…'}
            </button>
          )}
          <button
            type="button"
            className="niuu-bg-transparent niuu-border niuu-border-solid niuu-border-brand niuu-rounded-md niuu-text-brand niuu-font-sans niuu-text-xs niuu-py-1.5 niuu-px-3 niuu-cursor-pointer niuu-whitespace-nowrap hover:niuu-bg-brand/10 niuu-transition-colors"
            data-testid="btn-test"
          >
            Test
          </button>
          <button type="button" className={ACTION_BTN} data-testid="btn-dispatch">
            Dispatch
          </button>
        </div>

        {/* Canvas content */}
        <div className="niuu-flex-1 niuu-flex niuu-flex-col niuu-relative niuu-min-h-0">
          {view === 'graph' && (
            <GraphView
              nodes={workflow.nodes}
              edges={workflow.edges}
              selectedNodeId={selectedNodeId}
              connectingFromId={connectingFromId}
              connectingFromLabel={connectingFromLabel}
              onSelectNode={selectNode}
              onInspectNode={inspectNode}
              onAddNode={addNode}
              onAddMimirResource={addMimirResource}
              onDeleteNode={deleteNode}
              onDeleteEdge={deleteEdge}
              onMoveNode={moveNode}
              onStartConnect={startConnect}
              onCancelConnect={cancelConnect}
              onCompleteConnect={completeConnect}
              onAddPersonaToStage={addPersonaToStage}
              onAddStageWithPersona={addStageWithPersona}
              personas={personas ?? DEFAULT_PERSONAS}
              issues={issues}
            />
          )}
          {view === 'pipeline' && (
            <PipelineView
              nodes={workflow.nodes}
              edges={workflow.edges}
              selectedNodeId={selectedNodeId}
              onSelectNode={selectNode}
              models={models}
            />
          )}
          {view === 'yaml' && <YamlView workflow={workflow} />}

          {/* ValidationPanel overlays the canvas */}
          <ValidationPanel
            workflow={workflow}
            onSelectNode={selectNode}
            errorCount={errorCount}
            warnCount={warnCount}
          />
        </div>
      </div>

      {/* Detail panel — right side */}
      <WorkflowDetailPanel
        workflow={workflow}
        selectedNode={selectedNode}
        errorCount={errorCount}
        warnCount={warnCount}
        issues={issues}
        personas={personas ?? DEFAULT_PERSONAS}
        models={models}
        registryMounts={registryMounts}
        onDeleteNode={deleteNode}
        onUpdateNode={updateNode}
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
      />
    </div>
  );
}
