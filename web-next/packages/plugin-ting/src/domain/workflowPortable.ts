import { dump } from 'js-yaml';
import type { Workflow, WorkflowPersonaDependency } from './workflow';

export const WORKFLOW_SCHEMA_VERSION = 1 as const;

export interface PortableWorkflowDocument {
  schema_version: 1 | 2;
  workflow_dependencies?: Record<string, WorkflowPersonaDependency>;
  id: string;
  name: string;
  description: string;
  version: string;
  graph: Record<string, unknown>;
  persona_dependencies: Record<string, WorkflowPersonaDependency>;
}

/**
 * Build the exact portable definition represented by the editor.
 *
 * Unknown graph keys received from the service are retained. Fields the builder
 * edits are overlaid so a YAML view or save always reflects the current draft.
 */
export function toPortableWorkflowDocument(workflow: Workflow): PortableWorkflowDocument {
  return {
    schema_version: workflow.schemaVersion ?? WORKFLOW_SCHEMA_VERSION,
    ...(workflow.schemaVersion === 2
      ? { workflow_dependencies: workflow.workflowDependencies ?? {} }
      : {}),
    id: workflow.id,
    name: workflow.name,
    description: workflow.description ?? '',
    version: workflow.version ?? 'draft',
    persona_dependencies: workflow.personaDependencies ?? {},
    graph: {
      ...(workflow.graph ?? {}),
      tags: workflow.tags ?? [],
      nodes: workflow.nodes,
      edges: workflow.edges,
      resourceBindings: workflow.resourceBindings ?? [],
    },
  };
}

/** Serialize the canonical portable shape without dropping multiline or unknown fields. */
export function serializePortableWorkflow(workflow: Workflow): string {
  return dump(toPortableWorkflowDocument(workflow), {
    noRefs: true,
    sortKeys: false,
    lineWidth: 100,
  });
}
