import { useForgeProjects } from '../application/useProjects';
import { NEW_PROJECT, type ProjectChoice } from '../domain/projects';

const CONTROL =
  'niuu:w-full niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-tertiary niuu:px-2.5 niuu:py-1.5 niuu:text-sm niuu:text-text-primary';

/** Which project a workspace belongs to: an existing one, a new one, or none. */
export function ProjectPicker({
  value,
  onChange,
  testId = 'project-picker',
}: {
  value: ProjectChoice;
  onChange: (next: ProjectChoice) => void;
  testId?: string;
}) {
  const projects = useForgeProjects();
  return (
    <div className="niuu:flex niuu:flex-col niuu:gap-2">
      <select
        aria-label="Project"
        className={CONTROL}
        value={value.projectId}
        onChange={(event) => onChange({ ...value, projectId: event.target.value })}
        data-testid={testId}
      >
        <option value="">No project</option>
        {(projects.data ?? []).map((project) => (
          <option key={project.id} value={project.id}>
            {project.name}
          </option>
        ))}
        <option value={NEW_PROJECT}>New project…</option>
      </select>
      {value.projectId === NEW_PROJECT ? (
        <input
          aria-label="New project name"
          className={CONTROL}
          placeholder="Named after this workspace if left empty"
          value={value.newProjectName}
          onChange={(event) => onChange({ ...value, newProjectName: event.target.value })}
          data-testid={`${testId}-name`}
        />
      ) : null}
      {projects.error ? (
        <span className="niuu:text-xs niuu:text-critical-fg">
          Could not load projects: {String(projects.error)}
        </span>
      ) : null}
    </div>
  );
}
