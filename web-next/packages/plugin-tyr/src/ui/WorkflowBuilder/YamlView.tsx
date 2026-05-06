/**
 * YamlView — read-only pretty-printed YAML of the Workflow DAG.
 *
 * Uses the custom `workflowToYaml` serialiser from graphUtils.
 * No external YAML library dependency.
 *
 * Owner: plugin-tyr (WorkflowBuilder).
 */

import type { Workflow } from '../../domain/workflow';
import { workflowToYaml } from './graphUtils';

export interface YamlViewProps {
  workflow: Workflow;
}

export function YamlView({ workflow }: YamlViewProps) {
  const yaml = workflowToYaml(workflow);

  return (
    <div data-testid="yaml-view" className="niuu-flex-1 niuu-overflow-auto niuu-bg-bg-primary">
      <pre
        data-testid="yaml-content"
        className="niuu-m-0 niuu-p-6 niuu-font-mono niuu-text-xs niuu-leading-relaxed niuu-text-text-secondary niuu-whitespace-pre"
      >
        {yaml}
      </pre>
    </div>
  );
}
