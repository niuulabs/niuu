/**
 * WorkflowBuilderPage — the /ting/workflows route component.
 *
 * Layout matches web2 prototype: templates sidebar on the left with a list
 * of saved workflows + working copy section, and the full WorkflowBuilder
 * filling the remaining space.
 *
 * Owner: plugin-ting.
 */

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import type { IBifrostService } from '@niuulabs/plugin-bifrost';
import { useService } from '@niuulabs/plugin-sdk';
import { StateDot, cn } from '@niuulabs/ui';
import type { Workflow } from '../domain/workflow';
import {
  useWorkflows,
  useCreateWorkflow,
  useDeleteWorkflow,
  useSaveWorkflow,
} from './useWorkflows';
import { usePersonasBrowser } from './settings/usePersonasBrowser';
import { WorkflowBuilder } from './WorkflowBuilder';
import type { PersonaEntry } from './WorkflowBuilder/LibraryPanel';
import type { WorkflowStageModelOption } from './WorkflowBuilder/useWorkflowBuilder';
import { useWorkflowRegistryMounts } from './useWorkflowRegistryMounts';

function formatModelOption(
  id: string,
  model?: { name?: string; vendor?: string; provider?: string; tier?: string },
): string {
  if (!model) return id;
  const parts = [model.name || id, model.vendor || model.provider];
  if (model.tier) parts.push(model.tier);
  return parts.filter(Boolean).join(' · ');
}

export function WorkflowBuilderPage() {
  const bifrost = useService<IBifrostService>('bifrost');
  const { data: workflows, isLoading, isError, error } = useWorkflows();
  const { data: personas } = usePersonasBrowser();
  const { data: registryMounts = [] } = useWorkflowRegistryMounts();
  const modelsQuery = useQuery({
    queryKey: ['bifrost', 'models'],
    queryFn: () => bifrost.getModelCatalog(),
  });
  const [activeWorkflow, setActiveWorkflow] = useState<Workflow | null>(null);
  const createMutation = useCreateWorkflow();
  const saveMutation = useSaveWorkflow();
  const deleteMutation = useDeleteWorkflow();

  const displayed = activeWorkflow ?? workflows?.[0] ?? null;
  const activeCount = workflows?.length ?? 0;

  const workflowPersonas: PersonaEntry[] | undefined = personas?.map((persona) => ({
    id: persona.name,
    label: persona.name,
    role: persona.role ?? 'build',
    produces: persona.producesEvent ? [persona.producesEvent] : [],
    consumes: persona.consumesEvents ?? [],
  }));
  const workflowModels: WorkflowStageModelOption[] = Object.entries(modelsQuery.data ?? {})
    .map(([id, model]) => ({
      id,
      label: formatModelOption(id, model),
      vendor: model.vendor,
    }))
    .sort((left, right) => left.label.localeCompare(right.label));

  function handleNew() {
    createMutation.mutate(
      {},
      {
        onSuccess: (newWf) => setActiveWorkflow(newWf),
      },
    );
  }

  function handleDelete(id: string) {
    if (!window.confirm('Delete this workflow?')) return;
    deleteMutation.mutate(id, {
      onSuccess: () => setActiveWorkflow(null),
    });
  }

  return (
    <div
      data-testid="workflow-builder-page"
      className="niuu-flex niuu-h-full niuu-font-sans niuu-bg-bg-primary"
    >
      {/* Templates sidebar */}
      <aside className="niuu-w-[244px] niuu-shrink-0 niuu-border-r niuu-border-border niuu-bg-bg-secondary niuu-flex niuu-flex-col niuu-overflow-hidden">
        {/* Header */}
        <div className="niuu-flex niuu-items-start niuu-justify-between niuu-px-4 niuu-pt-3 niuu-pb-1">
          <div className="niuu-flex niuu-flex-col niuu-gap-0.5">
            <span className="niuu-text-[10px] niuu-font-semibold niuu-uppercase niuu-tracking-[0.24em] niuu-text-text-muted niuu-font-sans">
              Templates
            </span>
            <span className="niuu-text-[11px] niuu-font-semibold niuu-text-text-primary niuu-font-sans">
              Saved workflow catalog
            </span>
          </div>
          <button
            data-testid="new-workflow"
            onClick={handleNew}
            disabled={createMutation.isPending}
            className="niuu-rounded-md niuu-px-2.5 niuu-py-1 niuu-text-[10px] niuu-border niuu-border-border niuu-bg-bg-elevated niuu-text-text-secondary niuu-cursor-pointer hover:niuu-text-text-primary niuu-transition-colors niuu-font-sans disabled:niuu-opacity-50"
          >
            + new
          </button>
        </div>
        <div className="niuu-px-4 niuu-pb-2 niuu-flex niuu-items-end niuu-justify-between niuu-gap-3">
          <p className="niuu-text-[10px] niuu-text-text-faint niuu-font-mono niuu-m-0 niuu-leading-snug">
            Reusable saga pipelines.{'\n'}Versioned, used by dispatch.
          </p>
          <span className="niuu-text-[10px] niuu-text-text-faint niuu-font-mono niuu-shrink-0">
            {activeCount} total
          </span>
        </div>

        {/* Template list */}
        <div className="niuu-flex-1 niuu-overflow-y-auto niuu-px-2.5 niuu-pb-3">
          {isLoading && (
            <div className="niuu-flex niuu-items-center niuu-gap-2 niuu-text-text-secondary niuu-text-xs niuu-px-2 niuu-py-3">
              <StateDot state="processing" pulse />
              <span>Loading…</span>
            </div>
          )}

          {isError && (
            <div className="niuu-flex niuu-items-center niuu-gap-2 niuu-text-critical niuu-text-xs niuu-px-2 niuu-py-3">
              <StateDot state="failed" />
              <span>{error instanceof Error ? error.message : 'Load failed'}</span>
            </div>
          )}

          {workflows?.map((wf: Workflow) => {
            const isActive = displayed?.id === wf.id;
            return (
              <button
                key={wf.id}
                data-testid={`workflow-tab-${wf.id}`}
                onClick={() => setActiveWorkflow(wf)}
                className={cn(
                  'niuu-flex niuu-items-start niuu-gap-2.5 niuu-w-full niuu-px-3 niuu-py-2.5 niuu-rounded-md niuu-border niuu-text-left niuu-font-sans niuu-text-xs niuu-cursor-pointer niuu-transition-colors',
                  isActive
                    ? 'niuu-bg-bg-elevated niuu-border-border niuu-text-text-primary'
                    : 'niuu-bg-transparent niuu-border-transparent niuu-text-text-secondary hover:niuu-bg-bg-tertiary hover:niuu-border-border-subtle',
                )}
              >
                <span className="niuu-text-brand niuu-text-sm niuu-leading-none niuu-mt-0.5">
                  ◇
                </span>
                <span className="niuu-flex-1 niuu-min-w-0 niuu-flex niuu-flex-col niuu-gap-1">
                  <span className="niuu-truncate niuu-font-semibold niuu-text-text-primary">
                    {wf.name.length > 22 ? wf.name.slice(0, 20) + '…' : wf.name}
                  </span>
                  <span className="niuu-flex niuu-items-center niuu-gap-2 niuu-text-[10px] niuu-font-mono niuu-text-text-faint">
                    {wf.version && <span className="niuu-shrink-0">v{wf.version}</span>}
                    <span className="niuu-shrink-0">{wf.nodes.length} nodes</span>
                    <span className="niuu-shrink-0">{wf.edges.length} edges</span>
                  </span>
                </span>
              </button>
            );
          })}
        </div>

        {/* Working copy */}
        <div className="niuu-border-t niuu-border-border niuu-px-4 niuu-py-2.5">
          <span className="niuu-text-[10px] niuu-font-semibold niuu-uppercase niuu-tracking-[0.24em] niuu-text-text-muted niuu-font-sans">
            Working Copy
          </span>
          <div className="niuu-flex niuu-items-center niuu-gap-2 niuu-mt-1.5">
            <span className="niuu-text-brand niuu-text-xs">◆</span>
            <span className="niuu-text-xs niuu-text-text-primary niuu-font-sans niuu-font-semibold">
              Current draft
            </span>
            <span className="niuu-text-[10px] niuu-text-text-faint niuu-font-mono niuu-ml-auto niuu-uppercase niuu-tracking-wide">
              unsaved
            </span>
          </div>
        </div>

        {/* Delete active */}
        {displayed && (
          <div className="niuu-border-t niuu-border-border niuu-px-4 niuu-py-2">
            <button
              data-testid={`delete-workflow-${displayed.id}`}
              onClick={() => handleDelete(displayed.id)}
              className="niuu-text-[10px] niuu-text-text-faint niuu-bg-transparent niuu-border-none niuu-cursor-pointer niuu-p-0 hover:niuu-text-critical niuu-transition-colors niuu-font-sans"
            >
              Delete workflow
            </button>
          </div>
        )}
      </aside>

      {/* Workflow builder */}
      {displayed && (
        <div className="niuu-flex-1 niuu-flex niuu-flex-col niuu-min-h-0 niuu-min-w-0">
          <WorkflowBuilder
            key={displayed.id}
            initialWorkflow={displayed}
            personas={workflowPersonas}
            models={workflowModels}
            registryMounts={registryMounts}
            onSave={(updated) =>
              saveMutation.mutate(updated, {
                onSuccess: (saved) => setActiveWorkflow(saved),
              })
            }
          />
        </div>
      )}

      {!isLoading && !isError && !displayed && (
        <div className="niuu-flex-1 niuu-flex niuu-items-center niuu-justify-center niuu-text-text-muted niuu-text-sm">
          No workflows found.
        </div>
      )}
    </div>
  );
}
