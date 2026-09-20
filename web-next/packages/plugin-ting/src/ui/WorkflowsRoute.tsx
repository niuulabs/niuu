/**
 * WorkflowsRoute — /ting/workflows in whichever mode the operator chose.
 *
 * Simple mode gets the one-workflow page; Advanced keeps the full builder.
 * The route is the same either way, so a link works from both.
 *
 * Owner: plugin-ting.
 */

import { useUiMode } from '@niuulabs/shell';
import { Link } from '@tanstack/react-router';
import { SimpleWorkflowsPage } from './SimpleWorkflowsPage';
import { WorkflowBuilderPage } from './WorkflowBuilderPage';

export function WorkflowsRoute() {
  const mode = useUiMode();
  return (
    <div className="niuu:flex niuu:h-full niuu:flex-col">
      <nav aria-label="Workflow execution" className="niuu:px-4 niuu:py-2">
        <Link to={'/ting/workflows/runs' as never} className="niuu:text-brand">
          Developer workflow runs
        </Link>
      </nav>
      {mode === 'simple' ? <SimpleWorkflowsPage /> : <WorkflowBuilderPage />}
    </div>
  );
}
