import { useState } from 'react';
import { Link, useNavigate } from '@tanstack/react-router';
import { useQueryClient } from '@tanstack/react-query';
import {
  BoardWorkflowsCard,
  TrackerWorkImportDialog,
  mergeWorkPages,
  useSaga,
  useTrackerBoard,
  useWorkCollection,
  type Saga,
} from '@niuulabs/plugin-ting';
import { useCreateTrustGrant, type RealmSummary } from '@niuulabs/plugin-valkyrie';
import { SectionCard } from '@niuulabs/plugin-volundr';
import { Chip } from '@niuulabs/ui';
import { useForgeProjects, useSetRealmProject } from '../application/useProjects';
import type { ProjectChoice } from '../domain/projects';
import type { RealmView } from '../domain/realm';
import { ProjectPicker } from './ProjectPicker';

const BUTTON =
  'niuu:self-start niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:px-3 niuu:py-1.5 niuu:text-xs niuu:font-medium niuu:text-text-primary niuu:disabled:opacity-40';
const LINK = 'niuu:text-xs niuu:text-brand-300';

function ProjectCard({ realm }: { realm: RealmSummary }) {
  const move = useSetRealmProject(realm.slug, realm.name);
  const projects = useForgeProjects();
  const current = realm.project_id ?? '';
  const [choice, setChoice] = useState<ProjectChoice>({ projectId: current, newProjectName: '' });
  const project = projects.data?.find((entry) => entry.id === current) ?? null;
  return (
    <SectionCard title="Project" description="Workspaces in a project share its brief.">
      <div className="niuu:flex niuu:flex-col niuu:gap-2" data-testid="connection-project">
        <ProjectPicker value={choice} onChange={setChoice} />
        <div className="niuu:flex niuu:items-center niuu:gap-3">
          <button
            type="button"
            className={BUTTON}
            disabled={choice.projectId === current || move.isPending}
            onClick={() => move.mutate(choice)}
          >
            {move.isPending ? 'Moving…' : 'Move workspace'}
          </button>
          {current ? (
            <Link
              to={'/realms/projects/$projectId' as never}
              params={{ projectId: current } as never}
              className={LINK}
            >
              {project ? `Open ${project.name}` : 'Open the project'}
            </Link>
          ) : null}
        </div>
        {move.error ? (
          <span className="niuu:text-xs niuu:text-critical-fg">{String(move.error)}</span>
        ) : null}
      </div>
    </SectionCard>
  );
}

function ConnectTracker({
  realm,
  view,
  onClose,
}: {
  realm: RealmSummary;
  view: RealmView;
  onClose: () => void;
}) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const work = useWorkCollection();
  const grant = useCreateTrustGrant(realm.slug);
  const connected = mergeWorkPages(work.data?.pages)?.projects ?? [];
  const binding = view.binding;

  function bind(saga: Saga) {
    // The latest observe grant is where a workspace looks, so this rebinds the board.
    grant.mutate(
      {
        action_class: 'observe',
        target: binding?.repo || '*',
        level: 2,
        limits: {
          template: binding?.template ?? '',
          repo: binding?.repo ?? '',
          branch: binding?.branch ?? '',
          tracker_board: saga.trackerId,
          bug_board: binding?.bugBoard ?? '',
          mount_target: binding?.mountTarget ?? '',
        },
      },
      {
        onSuccess: () => {
          void queryClient.invalidateQueries({ queryKey: ['ting'] });
          onClose();
        },
      },
    );
  }

  return (
    <TrackerWorkImportDialog
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      onImported={bind}
      connectedProjects={connected}
      onOpenProject={(project) =>
        void navigate({
          to: '/ting/work/$workId' as never,
          params: { workId: project.id } as never,
        })
      }
      forgeProjectId={realm.project_id ?? undefined}
    />
  );
}

function TrackerCard({ realm, view }: { realm: RealmSummary; view: RealmView }) {
  const board = useTrackerBoard(view.binding?.trackerBoard || null);
  const [connecting, setConnecting] = useState(false);
  const trackerBoard = view.binding?.trackerBoard;
  return (
    <SectionCard
      title="Tracker"
      description="This workspace's own board. Tickets stay in your tracker and show here live."
    >
      <div className="niuu:flex niuu:flex-col niuu:gap-2" data-testid="connection-tracker">
        {trackerBoard ? (
          <div className="niuu:flex niuu:items-center niuu:gap-2 niuu:text-sm niuu:text-text-primary">
            <Chip tone="brand">{board.saga?.trackerType ?? 'tracker'}</Chip>
            {board.saga?.name ?? trackerBoard}
          </div>
        ) : (
          <span className="niuu:text-xs niuu:text-text-faint">
            No tracker. A workspace works without one.
          </span>
        )}
        <button type="button" className={BUTTON} onClick={() => setConnecting(true)}>
          {trackerBoard ? 'Change tracker' : 'Connect a tracker'}
        </button>
      </div>
      {connecting ? (
        <ConnectTracker realm={realm} view={view} onClose={() => setConnecting(false)} />
      ) : null}
    </SectionCard>
  );
}

function RepositoryCard({ view }: { view: RealmView }) {
  return (
    <SectionCard title="Repository" description="Only work that changes code needs one.">
      <span className="niuu:font-mono niuu:text-xs niuu:text-text-secondary">
        {view.binding?.repo || 'None'}
      </span>
    </SectionCard>
  );
}

/** What a workspace is connected to, moved here from settings, Work and the wizard. */
export function ConnectionsPanel({ realm, view }: { realm: RealmSummary; view: RealmView }) {
  const board = useTrackerBoard(view.binding?.trackerBoard || null);
  // The list leaves out the board's workflows, so the card reads the project itself.
  const saga = useSaga(board.saga?.id ?? '');
  return (
    <div className="niuu:flex niuu:flex-col niuu:gap-4" data-testid="realm-connections">
      <div className="niuu:grid niuu:grid-cols-[repeat(auto-fit,minmax(260px,1fr))] niuu:gap-4">
        <ProjectCard realm={realm} />
        <TrackerCard realm={realm} view={view} />
        <RepositoryCard view={view} />
      </div>
      {saga.data ? <BoardWorkflowsCard saga={saga.data} /> : null}
    </div>
  );
}
