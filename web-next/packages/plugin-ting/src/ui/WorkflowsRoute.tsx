/**
 * WorkflowsRoute — /ting/workflows in whichever mode the operator chose.
 *
 * Simple mode gets the one-workflow page; Advanced keeps the full builder.
 * The route is the same either way, so a link works from both.
 *
 * Owner: plugin-ting.
 */

import { useUiMode } from '@niuulabs/shell';
import { SimpleWorkflowsPage } from './SimpleWorkflowsPage';
import { WorkflowBuilderPage } from './WorkflowBuilderPage';

export function WorkflowsRoute() {
  const mode = useUiMode();
  if (mode === 'simple') return <SimpleWorkflowsPage />;
  return <WorkflowBuilderPage />;
}
