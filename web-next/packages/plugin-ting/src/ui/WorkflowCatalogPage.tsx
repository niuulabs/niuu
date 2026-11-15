/**
 * WorkflowCatalogPage — /ting/workflows, the one place workflows live.
 *
 * Viewing: the workflow list on the left, the chosen workflow drawn on the
 * builder canvas (read-only), and a panel to run it with the runs recorded
 * for it. Edit turns the same canvas into the editor; the list and the run
 * panel step aside until the person saves or cancels.
 *
 * Owner: plugin-ting.
 */

import { useState } from 'react';
import { useNavigate, useSearch } from '@tanstack/react-router';
import { useQuery } from '@tanstack/react-query';
import type { IBifrostService } from '@niuulabs/plugin-bifrost';
import { useService } from '@niuulabs/plugin-sdk';
import {
  LoadingState,
  SidebarResizer,
  cn,
  useResizableSidebar,
  type SidebarBounds,
} from '@niuulabs/ui';
import { Workflow as WorkflowIcon } from 'lucide-react';
import type { Workflow } from '../domain/workflow';
import type { WorkflowExportFormat } from '../ports';
import {
  useCreateWorkflow,
  useDeleteWorkflow,
  useExportWorkflow,
  useLoadWorkflowVersion,
  useSaveWorkflow,
  useWorkflowVersions,
  useWorkflows,
} from './useWorkflows';
import { useResearchCampaigns } from './useResearch';
import { useSpecCampaigns } from './useSpecs';
import {
  WorkflowBuilder,
  type WorkflowEditorLocation,
  type WorkflowEditorMode,
} from './WorkflowBuilder';
import type { PersonaEntry } from './WorkflowBuilder/LibraryPanel';
import type { WorkflowStageModelOption } from './WorkflowBuilder/useWorkflowBuilder';
import { WorkflowRunPanel, humanGateCount, workflowSummary } from './WorkflowRunPanel';
import { WorkflowImportDialog } from './WorkflowImportDialog';
import { downloadWorkflowFile } from './workflowFiles';
import { usePersonasBrowser } from './settings/usePersonasBrowser';
import { useWorkflowRegistryMounts } from './useWorkflowRegistryMounts';
import './WorkflowCatalogPage.css';

interface WorkflowCatalogSearch {
  workflow?: string;
  /** The id older builder links carried; read like `workflow`. */
  id?: string;
  repo?: string;
  branch?: string;
}

/**
 * Where the canvas is, for the workflow selected in the list: the version or
 * child workflow on display, whether it is being edited, and the parents
 * walked through to reach a child.
 */
interface EditorState {
  anchorId: string;
  location: WorkflowEditorLocation;
  ancestry: WorkflowEditorLocation[];
}

function stageCount(workflow: Workflow): number {
  return workflow.nodes.filter((node) => node.kind === 'stage').length;
}

function makeLocation(
  workflow: Workflow,
  mode: WorkflowEditorMode = 'view',
): WorkflowEditorLocation {
  return { workflow, savedWorkflow: workflow, mode };
}

function formatModelOption(
  id: string,
  model?: { name?: string; vendor?: string; provider?: string; tier?: string },
): string {
  if (!model) return id;
  const parts = [model.name || id, model.vendor || model.provider];
  if (model.tier) parts.push(model.tier);
  return parts.filter(Boolean).join(' · ');
}

function WorkflowOptionCard({
  workflow,
  selected,
  runCount,
  onSelect,
}: {
  workflow: Workflow;
  selected: boolean;
  runCount: number;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      data-testid={`catalog-workflow-${workflow.id}`}
      data-selected={selected ? 'true' : undefined}
      aria-current={selected ? 'page' : undefined}
      onClick={onSelect}
      className={cn(
        'niuu:flex niuu:w-full niuu:items-start niuu:gap-3 niuu:rounded-lg niuu:border niuu:px-3 niuu:py-2.5 niuu:text-left niuu:cursor-pointer niuu:transition-colors',
        selected
          ? 'niuu:border-brand/50 niuu:bg-brand/10'
          : 'niuu:border-transparent niuu:bg-transparent niuu:hover:border-border-subtle niuu:hover:bg-bg-tertiary',
      )}
    >
      <span className="niuu:mt-0.5 niuu:flex niuu:size-7 niuu:shrink-0 niuu:items-center niuu:justify-center niuu:rounded-full niuu:border niuu:border-border-subtle niuu:bg-bg-elevated niuu:text-text-secondary">
        <WorkflowIcon size={14} aria-hidden="true" />
      </span>
      <span className="niuu:flex niuu:min-w-0 niuu:flex-1 niuu:flex-col niuu:gap-0.5">
        <span className="ting-workflow-catalog__row-title niuu:truncate niuu:font-semibold niuu:text-text-primary">
          {workflow.name}
        </span>
        <span className="niuu:flex niuu:min-w-0 niuu:flex-col niuu:gap-1">
          <span className="niuu:min-w-0 niuu:flex-1 niuu:truncate niuu:text-xs niuu:text-text-muted">
            {workflowSummary(workflow)}
          </span>
          <span className="niuu:shrink-0 niuu:font-mono niuu:text-xs niuu:text-text-faint">
            {humanGateCount(workflow)} gates · {stageCount(workflow)} stages
            {runCount > 0 ? ` · ran ${runCount}×` : ''}
          </span>
        </span>
      </span>
    </button>
  );
}

/** How many campaign runs each workflow has, keyed by workflow id. */
function useWorkflowRunCounts(): Record<string, number> {
  const specs = useSpecCampaigns();
  const research = useResearchCampaigns();
  const counts: Record<string, number> = {};
  for (const campaign of [...(specs.data ?? []), ...(research.data ?? [])]) {
    counts[campaign.workflowId] = (counts[campaign.workflowId] ?? 0) + 1;
  }
  return counts;
}

/** The workflow list's width bounds; the person drags its edge between them. */
const CATALOG_SIDEBAR: SidebarBounds = { default: 320, min: 240, max: 560, keyboardStep: 20 };

export function WorkflowCatalogPage() {
  const sidebar = useResizableSidebar('niuu.work.workflowsSidebarWidth', CATALOG_SIDEBAR);
  const navigate = useNavigate();
  const search = useSearch({ strict: false }) as WorkflowCatalogSearch;
  const bifrost = useService<IBifrostService>('bifrost');
  const { data: workflows, isLoading, isError, error } = useWorkflows();
  const runCounts = useWorkflowRunCounts();
  const { data: personas } = usePersonasBrowser();
  const { data: registryMounts = [] } = useWorkflowRegistryMounts();
  const modelsQuery = useQuery({
    queryKey: ['bifrost', 'model-catalog'],
    queryFn: () => bifrost.getModelCatalog(),
  });
  const createWorkflow = useCreateWorkflow();
  const saveMutation = useSaveWorkflow();
  const deleteMutation = useDeleteWorkflow();
  const exportMutation = useExportWorkflow();
  const loadVersionMutation = useLoadWorkflowVersion();
  const [importOpen, setImportOpen] = useState(false);
  const [editor, setEditor] = useState<EditorState | null>(null);

  const requestedId = search.workflow ?? search.id;
  const selected =
    workflows?.find((workflow) => workflow.id === requestedId) ?? workflows?.[0] ?? null;
  const active = selected && editor?.anchorId === selected.id ? editor : null;
  const location = active?.location ?? (selected ? makeLocation(selected) : null);
  const ancestry = active?.ancestry ?? [];
  const displayed = location?.workflow ?? null;
  const editing = location?.mode === 'edit';
  const versionsQuery = useWorkflowVersions(displayed?.id ?? '');

  const workflowPersonas: PersonaEntry[] | undefined = personas?.map((persona) => {
    const outcomeEvents = persona.outcomeEvents ?? {};
    const produces = [...new Set([...Object.values(outcomeEvents), persona.producesEvent])].filter(
      Boolean,
    );
    return {
      id: persona.name,
      label: persona.name,
      role: persona.role ?? 'build',
      produces,
      outcomeEvents,
      consumes: persona.consumesEvents ?? [],
    };
  });
  const workflowModels: WorkflowStageModelOption[] = Object.entries(modelsQuery.data ?? {})
    .map(([id, model]) => ({ id, label: formatModelOption(id, model), vendor: model.vendor }))
    .sort((left, right) => left.label.localeCompare(right.label));

  /** Changes what the canvas shows for the selected workflow. */
  function updateEditor(
    update: (
      current: Pick<EditorState, 'location' | 'ancestry'>,
    ) => Pick<EditorState, 'location' | 'ancestry'>,
  ) {
    if (!selected || !location) return;
    const current = { location, ancestry };
    setEditor({ anchorId: selected.id, ...update(current) });
  }

  function selectWorkflow(id: string) {
    setEditor(null);
    void navigate({ to: '/ting/workflows', search: { ...search, id: undefined, workflow: id } });
  }

  function handleNew() {
    createWorkflow.mutate(undefined, {
      onSuccess: (created) => {
        void navigate({
          to: '/ting/workflows',
          search: { ...search, id: undefined, workflow: created.id },
        });
        setEditor({ anchorId: created.id, location: makeLocation(created, 'edit'), ancestry: [] });
      },
    });
  }

  function handleDelete(workflow: Workflow) {
    if (!window.confirm(`Delete "${workflow.name}"?`)) return;
    deleteMutation.mutate(workflow.id, {
      onSuccess: () => {
        setEditor(null);
        void navigate({
          to: '/ting/workflows',
          search: { ...search, id: undefined, workflow: undefined },
        });
      },
    });
  }

  function handleExport(format: WorkflowExportFormat) {
    if (!displayed) return;
    exportMutation.mutate(
      { id: displayed.id, format, version: displayed.version },
      { onSuccess: downloadWorkflowFile },
    );
  }

  function handleSelectVersion(version: string) {
    if (!displayed || version === displayed.version) return;
    loadVersionMutation.mutate(
      { id: displayed.id, version },
      {
        onSuccess: (workflow) =>
          updateEditor((current) => ({ ...current, location: makeLocation(workflow) })),
      },
    );
  }

  const editorError =
    saveMutation.error ??
    exportMutation.error ??
    deleteMutation.error ??
    versionsQuery.error ??
    loadVersionMutation.error;

  return (
    <div
      data-testid="workflow-catalog-page"
      data-editing={editing ? 'true' : undefined}
      className="ting-workflow-catalog"
    >
      {editing ? null : (
        <>
          <aside
            className="ting-workflow-catalog__sidebar"
            aria-label="Workflow catalog"
            style={{ flexBasis: `${sidebar.width}px` }}
          >
            <div className="ting-workflow-catalog__heading">
              <h2>Workflows</h2>
              <div className="niuu:flex niuu:items-center niuu:gap-2">
                <button
                  type="button"
                  data-testid="catalog-workflow-import"
                  onClick={() => setImportOpen(true)}
                  className="niuu:rounded-md niuu:border niuu:border-border niuu:bg-bg-elevated niuu:px-2.5 niuu:py-1 niuu:text-xs niuu:text-text-secondary niuu:cursor-pointer niuu:hover:text-text-primary"
                >
                  Import
                </button>
                <button
                  type="button"
                  data-testid="catalog-workflow-new"
                  onClick={handleNew}
                  disabled={createWorkflow.isPending}
                  className="niuu:rounded-md niuu:border niuu:border-border niuu:bg-bg-elevated niuu:px-2.5 niuu:py-1 niuu:text-xs niuu:text-text-secondary niuu:cursor-pointer niuu:hover:text-text-primary niuu:disabled:opacity-50"
                >
                  New
                </button>
              </div>
            </div>

            <div className="niuu:flex niuu:flex-1 niuu:flex-col niuu:gap-1 niuu:overflow-y-auto niuu:px-3 niuu:pb-3">
              {isLoading ? <LoadingState label="Loading workflows…" /> : null}
              {isError ? (
                <p className="niuu:m-0 niuu:px-2 niuu:text-xs niuu:text-critical" role="alert">
                  {error instanceof Error ? error.message : 'Failed to load workflows.'}
                </p>
              ) : null}
              {createWorkflow.error ? (
                <p className="niuu:m-0 niuu:px-2 niuu:text-xs niuu:text-critical" role="alert">
                  Could not create a workflow: {createWorkflow.error.message}
                </p>
              ) : null}
              {!isLoading && !isError && (workflows?.length ?? 0) === 0 ? (
                <p className="niuu:m-0 niuu:px-2 niuu:text-xs niuu:text-text-muted">
                  No workflows yet. New builds your first one.
                </p>
              ) : null}
              {workflows?.map((workflow) => (
                <WorkflowOptionCard
                  key={workflow.id}
                  workflow={workflow}
                  selected={workflow.id === selected?.id}
                  runCount={runCounts[workflow.id] ?? 0}
                  onSelect={() => selectWorkflow(workflow.id)}
                />
              ))}
            </div>
          </aside>
          <SidebarResizer
            label="Resize workflow list"
            className="ting-workflow-catalog__resizer"
            {...sidebar.resizer}
          />
        </>
      )}

      <main className="ting-workflow-catalog__canvas" aria-label="Workflow">
        {!isLoading && !isError && !displayed ? (
          <p className="niuu:m-auto niuu:text-sm niuu:text-text-muted">No workflow selected yet.</p>
        ) : null}
        {displayed ? (
          <WorkflowBuilder
            key={`${displayed.id}:${displayed.documentRevision ?? displayed.version ?? 'head'}`}
            initialWorkflow={displayed}
            initialSavedWorkflow={location?.savedWorkflow}
            mode={location?.mode}
            ancestry={ancestry}
            versions={versionsQuery.data ?? []}
            versionPending={loadVersionMutation.isPending}
            onSelectVersion={handleSelectVersion}
            workflowCatalog={workflows ?? []}
            onOpenWorkflow={(workflow, current) =>
              updateEditor((state) => ({
                location: makeLocation(workflow),
                ancestry: [...state.ancestry, current],
              }))
            }
            onNavigateAncestor={(index) =>
              updateEditor((state) => {
                const parent = state.ancestry[index];
                if (!parent) return state;
                return { location: parent, ancestry: state.ancestry.slice(0, index) };
              })
            }
            onDeleteWorkflow={handleDelete}
            personas={workflowPersonas}
            models={workflowModels}
            registryMounts={registryMounts}
            onSave={(updated) => saveMutation.mutateAsync(updated)}
            onSaved={(saved) =>
              updateEditor((state) => ({ ...state, location: makeLocation(saved) }))
            }
            savePending={saveMutation.isPending}
            onEdit={(workflow) =>
              updateEditor((state) => ({
                ...state,
                location: {
                  workflow,
                  savedWorkflow:
                    state.location.workflow.id === workflow.id
                      ? state.location.savedWorkflow
                      : workflow,
                  mode: 'edit',
                },
              }))
            }
            onCancelEdit={() =>
              updateEditor((state) => ({
                ...state,
                location: makeLocation(state.location.savedWorkflow),
              }))
            }
            onRefreshPersona={(workflow, alias) =>
              saveMutation.mutate(
                { ...workflow, refreshPersonas: [alias] },
                {
                  onSuccess: (saved) =>
                    updateEditor((state) => ({
                      ...state,
                      location: { ...makeLocation(saved), mode: state.location.mode },
                    })),
                },
              )
            }
            onExport={handleExport}
            exportPending={exportMutation.isPending}
          />
        ) : null}
        {editorError ? (
          <div
            role="alert"
            className="niuu:border-t niuu:border-critical niuu:bg-bg-secondary niuu:px-4 niuu:py-2 niuu:text-xs niuu:text-critical"
          >
            {editorError.message}
          </div>
        ) : null}
      </main>

      {!editing && displayed ? (
        <WorkflowRunPanel
          key={`${displayed.id}:${displayed.version ?? 'head'}`}
          workflow={displayed}
          initialRepo={search.repo}
          initialBranch={search.branch}
        />
      ) : null}

      {importOpen ? (
        <WorkflowImportDialog
          open
          workflows={workflows ?? []}
          personas={personas ?? []}
          registryMounts={registryMounts}
          onClose={() => setImportOpen(false)}
          onImported={(workflow) => selectWorkflow(workflow.id)}
        />
      ) : null}
    </div>
  );
}
