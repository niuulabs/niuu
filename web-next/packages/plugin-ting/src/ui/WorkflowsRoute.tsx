/**
 * WorkflowsRoute — the stable workflow catalog in either interface mode.
 * Full-screen authoring remains at /ting/workflows/build.
 *
 * Owner: plugin-ting.
 */

import { SimpleWorkflowsPage } from './SimpleWorkflowsPage';
import { WorkflowBuilderPage } from './WorkflowBuilderPage';
import { useSearch } from '@tanstack/react-router';

export function WorkflowsRoute() {
  const search = useSearch({ strict: false }) as { id?: string };
  if (typeof search.id === 'string' && search.id.trim()) {
    return <WorkflowBuilderPage />;
  }
  return <SimpleWorkflowsPage />;
}
