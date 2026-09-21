/** The full-canvas workflow editor route. */

import { useState } from 'react';
import { useSearch } from '@tanstack/react-router';
import { useQuery } from '@tanstack/react-query';
import type { IBifrostService } from '@niuulabs/plugin-bifrost';
import { useService } from '@niuulabs/plugin-sdk';
import { StateDot, type RepoRecord } from '@niuulabs/ui';
import type { Workflow } from '../domain/workflow';
import type { WorkflowExportFormat, WorkflowLaunchRequest } from '../ports';
import {
  useWorkflows,
  useCreateWorkflow,
  useDeleteWorkflow,
  useSaveWorkflow,
  useLaunchWorkflow,
  useExportWorkflow,
  useLoadWorkflowVersion,
  useWorkflowVersions,
} from './useWorkflows';
import { usePersonasBrowser } from './settings/usePersonasBrowser';
import {
  WorkflowBuilder,
  type WorkflowEditorLocation,
  type WorkflowEditorMode,
} from './WorkflowBuilder';
import type { PersonaEntry } from './WorkflowBuilder/LibraryPanel';
import type { WorkflowStageModelOption } from './WorkflowBuilder/useWorkflowBuilder';
import { useWorkflowRegistryMounts } from './useWorkflowRegistryMounts';
import { WorkflowLaunchModal } from './WorkflowLaunchModal';
import { WorkflowImportDialog } from './WorkflowImportDialog';
import { downloadWorkflowFile } from './workflowFiles';

type RepoCatalogService = {
  getRepos(): Promise<RepoRecord[]>;
  getBranches(repoUrl: string): Promise<string[]>;
};

function formatModelOption(
  id: string,
  model?: { name?: string; vendor?: string; provider?: string; tier?: string },
): string {
  if (!model) return id;
  const parts = [model.name || id, model.vendor || model.provider];
  if (model.tier) parts.push(model.tier);
  return parts.filter(Boolean).join(' · ');
}

interface WorkflowBuilderSearch {
  id?: string;
}

export function WorkflowBuilderPage() {
  const search = useSearch({ strict: false }) as WorkflowBuilderSearch;
  const bifrost = useService<IBifrostService>('bifrost');
  const repoCatalog = useService<RepoCatalogService>('niuu.repos');
  const { data: workflows, isLoading, isError, error } = useWorkflows();
  const { data: personas } = usePersonasBrowser();
  const { data: registryMounts = [] } = useWorkflowRegistryMounts();
  const modelsQuery = useQuery({
    queryKey: ['bifrost', 'models'],
    queryFn: () => bifrost.getModelCatalog(),
  });
  const reposQuery = useQuery({
    queryKey: ['niuu', 'repos'],
    queryFn: () => repoCatalog.getRepos(),
  });
  const [activeLocation, setActiveLocation] = useState<WorkflowEditorLocation | null>(null);
  const [ancestry, setAncestry] = useState<WorkflowEditorLocation[]>([]);
  const createMutation = useCreateWorkflow();
  const saveMutation = useSaveWorkflow();
  const deleteMutation = useDeleteWorkflow();
  const launchMutation = useLaunchWorkflow();
  const exportMutation = useExportWorkflow();
  const loadVersionMutation = useLoadWorkflowVersion();
  const [showLaunchModal, setShowLaunchModal] = useState(false);
  const [showImportDialog, setShowImportDialog] = useState(false);

  const requested = search.id ? (workflows?.find((item) => item.id === search.id) ?? null) : null;
  const catalogWorkflow = requested ?? workflows?.[0] ?? null;
  const location =
    activeLocation ??
    (catalogWorkflow
      ? { workflow: catalogWorkflow, savedWorkflow: catalogWorkflow, mode: 'view' as const }
      : null);
  const displayed = location?.workflow ?? null;
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

  function makeLocation(
    workflow: Workflow,
    mode: WorkflowEditorMode = 'view',
  ): WorkflowEditorLocation {
    return { workflow, savedWorkflow: workflow, mode };
  }

  function handleNew() {
    createMutation.mutate(
      {},
      {
        onSuccess: (workflow) => {
          setActiveLocation(makeLocation(workflow, 'edit'));
          setAncestry([]);
        },
      },
    );
  }

  function handleDelete(workflow: Workflow) {
    if (!window.confirm(`Delete "${workflow.name}"?`)) return;
    deleteMutation.mutate(workflow.id, {
      onSuccess: () => {
        setActiveLocation(null);
        setAncestry([]);
      },
    });
  }

  async function handleLaunch(request: WorkflowLaunchRequest) {
    if (!displayed) return;
    const result = await launchMutation.mutateAsync({
      workflowId: displayed.id,
      request: { ...request, workflowVersion: displayed.version },
    });
    setShowLaunchModal(false);
    window.location.assign(`/volundr/sessions/${encodeURIComponent(result.sessionId)}`);
  }

  function handleExport(format: WorkflowExportFormat) {
    if (!displayed) return;
    exportMutation.mutate(
      { id: displayed.id, format, version: displayed.version },
      { onSuccess: downloadWorkflowFile },
    );
  }

  function handleSelectWorkflow(workflow: Workflow) {
    setActiveLocation(makeLocation(workflow));
    setAncestry([]);
  }

  function handleOpenWorkflow(workflow: Workflow, current: WorkflowEditorLocation) {
    setAncestry((parents) => [...parents, current]);
    setActiveLocation(makeLocation(workflow));
  }

  function handleNavigateAncestor(index: number) {
    const parent = ancestry[index];
    if (!parent) return;
    setAncestry((parents) => parents.slice(0, index));
    setActiveLocation(parent);
  }

  function handleSelectVersion(version: string) {
    if (!displayed || version === displayed.version) return;
    loadVersionMutation.mutate(
      { id: displayed.id, version },
      {
        onSuccess: (workflow) => {
          setActiveLocation(makeLocation(workflow));
        },
      },
    );
  }

  if (isLoading) {
    return (
      <div
        data-testid="workflow-builder-page"
        className="niuu:flex niuu:h-full niuu:items-center niuu:justify-center niuu:gap-2 niuu:bg-bg-primary niuu:text-sm niuu:text-text-secondary"
      >
        <StateDot state="processing" pulse /> Loading workflows…
      </div>
    );
  }

  if (isError) {
    return (
      <div
        data-testid="workflow-builder-page"
        role="alert"
        className="niuu:flex niuu:h-full niuu:items-center niuu:justify-center niuu:gap-2 niuu:bg-bg-primary niuu:text-sm niuu:text-critical"
      >
        <StateDot state="failed" /> {error instanceof Error ? error.message : 'Load failed'}
      </div>
    );
  }

  if (!displayed) {
    return (
      <div
        data-testid="workflow-builder-page"
        className="niuu:flex niuu:h-full niuu:flex-col niuu:items-center niuu:justify-center niuu:gap-3 niuu:bg-bg-primary niuu:text-sm niuu:text-text-muted"
      >
        <span>No workflows found.</span>
        <button
          type="button"
          data-testid="new-workflow"
          onClick={handleNew}
          className="niuu:rounded-md niuu:border niuu:border-border niuu:bg-bg-elevated niuu:px-3 niuu:py-2 niuu:text-xs niuu:text-text-primary"
        >
          Create workflow
        </button>
      </div>
    );
  }

  return (
    <div
      data-testid="workflow-builder-page"
      className="niuu:flex niuu:h-full niuu:min-h-0 niuu:flex-col niuu:bg-bg-primary niuu:font-sans"
    >
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
        onSelectWorkflow={handleSelectWorkflow}
        onOpenWorkflow={handleOpenWorkflow}
        onNavigateAncestor={handleNavigateAncestor}
        onCreateWorkflow={handleNew}
        onImportWorkflow={() => setShowImportDialog(true)}
        onDeleteWorkflow={handleDelete}
        personas={workflowPersonas}
        models={workflowModels}
        registryMounts={registryMounts}
        onLaunch={() => setShowLaunchModal(true)}
        launchPending={launchMutation.isPending}
        onSave={(updated) => saveMutation.mutateAsync(updated)}
        onSaved={(saved) => {
          setActiveLocation(makeLocation(saved));
        }}
        savePending={saveMutation.isPending}
        onEdit={(workflow) => {
          setActiveLocation((current) => ({
            workflow,
            savedWorkflow: current?.workflow.id === workflow.id ? current.savedWorkflow : workflow,
            mode: 'edit',
          }));
        }}
        onCancelEdit={() =>
          setActiveLocation((current) =>
            current
              ? {
                  workflow: current.savedWorkflow,
                  savedWorkflow: current.savedWorkflow,
                  mode: 'view',
                }
              : current,
          )
        }
        onRefreshPersona={(workflow, alias) =>
          saveMutation.mutate(
            { ...workflow, refreshPersonas: [alias] },
            {
              onSuccess: (saved) =>
                setActiveLocation((current) => ({
                  workflow: saved,
                  savedWorkflow: saved,
                  mode: current?.mode ?? 'view',
                })),
            },
          )
        }
        onExport={handleExport}
        exportPending={exportMutation.isPending}
      />

      {saveMutation.error ||
      exportMutation.error ||
      versionsQuery.error ||
      loadVersionMutation.error ? (
        <div
          role="alert"
          className="niuu:border-t niuu:border-critical niuu:bg-bg-secondary niuu:px-4 niuu:py-2 niuu:text-xs niuu:text-critical"
        >
          {
            (
              saveMutation.error ??
              exportMutation.error ??
              versionsQuery.error ??
              loadVersionMutation.error
            )?.message
          }
        </div>
      ) : null}

      <WorkflowLaunchModal
        loadBranches={repoCatalog.getBranches}
        open={showLaunchModal}
        onOpenChange={setShowLaunchModal}
        workflow={displayed}
        repos={reposQuery.data ?? []}
        launching={launchMutation.isPending}
        onLaunch={handleLaunch}
      />

      {showImportDialog ? (
        <WorkflowImportDialog
          open
          workflows={workflows ?? []}
          personas={personas ?? []}
          registryMounts={registryMounts}
          onClose={() => setShowImportDialog(false)}
          onImported={(workflow) => {
            setActiveLocation(makeLocation(workflow));
            setAncestry([]);
          }}
        />
      ) : null}
    </div>
  );
}
