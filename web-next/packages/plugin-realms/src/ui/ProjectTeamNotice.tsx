import type { ForgeProject } from '@niuulabs/plugin-volundr';
import { errorDetail, useProjectTeamState } from '../application/useProjectIntegrations';

/** Says so when this cluster and the others do not have the same team; otherwise nothing. */
export function ProjectTeamNotice({ project }: { project: ForgeProject }) {
  const state = useProjectTeamState(project);
  const text = state.error ? errorDetail(state.error) : notice(state.data);
  if (!text) return null;
  return (
    <p
      role="status"
      className="niuu:m-0 niuu:text-xs niuu:text-critical-fg"
      data-testid="project-team-notice"
    >
      {text}
    </p>
  );
}

function notice(state: ReturnType<typeof useProjectTeamState>['data']): string {
  if (!state?.guild) return '';
  const { error, pending } = state.sync;
  if (state.home && (error || pending))
    return `Your other clusters do not have the latest team yet. ${error}`.trim();
  if (!state.home && error)
    return `The team below may be out of date on this cluster. ${error}`.trim();
  return '';
}
