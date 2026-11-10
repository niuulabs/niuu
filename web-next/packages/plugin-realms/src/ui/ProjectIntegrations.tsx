import { useState } from 'react';
import {
  SectionCard,
  type ForgeProject,
  type ProjectMember,
  type TeamCatalogEntry,
  type TeamDeliverTo,
  type TeamIntegration,
  type TeamRoute,
  type TeamRouteEvent,
} from '@niuulabs/plugin-volundr';
import { relTime } from '@niuulabs/ui';
import { canManageMembers, useProjectMembers, useViewer } from '../application/useProjectMembers';
import {
  errorDetail,
  isForbidden,
  routeFor,
  useRemoveTeamIntegration,
  useSetTeamRoute,
  useTeamCatalog,
  useTeamIntegrations,
  useTeamRoutes,
  useTeamUsage,
} from '../application/useProjectIntegrations';
import { ConnectIntegration } from './ProjectIntegrationConnect';
import { BUTTON, CONTROL } from './ProjectMembers';

/** The choices for where one kind of event goes, in the order they are offered. */
type Destination = TeamDeliverTo | 'none';

const ROUTE_ROWS: ReadonlyArray<{
  event: TeamRouteEvent;
  label: string;
  recipient?: string;
  destinations: readonly Destination[];
}> = [
  {
    event: 'needs_decision',
    label: 'Needs a decision',
    recipient: 'Message each approver',
    destinations: ['channel', 'recipient', 'none'],
  },
  {
    event: 'person_question',
    label: 'An agent asks a person',
    recipient: 'Message that person',
    destinations: ['recipient', 'channel', 'none'],
  },
  { event: 'progress', label: 'Progress and finished work', destinations: ['channel', 'none'] },
  {
    event: 'errors',
    label: 'Errors',
    recipient: 'Message the session owner',
    destinations: ['channel', 'recipient', 'none'],
  },
];

const MESSAGING = 'messaging';

function personName(members: readonly ProjectMember[], userId: string): string {
  const member = members.find((candidate) => candidate.user_id === userId);
  if (!member) return userId;
  return member.display_name || member.email || member.user_id;
}

function channelLabel(connection: TeamIntegration): string {
  const channel = String(connection.config.channel ?? '').trim();
  if (!channel) return 'Team channel';
  return channel.startsWith('#') ? channel : `#${channel}`;
}

function host(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}

function subLine(
  connection: TeamIntegration,
  entry: TeamCatalogEntry | undefined,
  members: readonly ProjectMember[],
): string {
  const parts: string[] = [];
  const site = String(connection.config.site_url ?? '').trim();
  if (site) parts.push(host(site));
  if (connection.integration_type === MESSAGING && connection.config.channel)
    parts.push(channelLabel(connection));
  if (entry?.team_install === 'client_credentials') parts.push('service account');
  if (connection.connected_by)
    parts.push(`installed by ${personName(members, connection.connected_by)}`);
  return parts.join(' · ');
}

function RouteGrid({
  project,
  connection,
  routes,
  manage,
  onError,
}: {
  project: ForgeProject;
  connection: TeamIntegration;
  routes: readonly TeamRoute[];
  manage: boolean;
  onError: (error: unknown) => void;
}) {
  const setRoute = useSetTeamRoute(project);
  const choose = (event: TeamRouteEvent, destination: Destination) =>
    setRoute.mutate(
      {
        event,
        route:
          destination === 'none'
            ? null
            : {
                connectionId: connection.id,
                deliverTo: destination,
                channel: destination === 'channel' ? String(connection.config.channel ?? '') : '',
              },
      },
      { onError },
    );

  return (
    <div
      className="niuu:grid niuu:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] niuu:items-center niuu:gap-x-3 niuu:gap-y-2 niuu:pl-10"
      data-testid={`project-integration-routes-${connection.id}`}
    >
      <span className="niuu:text-[11px] niuu:uppercase niuu:text-text-muted">When</span>
      <span className="niuu:text-[11px] niuu:uppercase niuu:text-text-muted">Send to</span>
      {ROUTE_ROWS.map((row) => {
        const current: Destination =
          routeFor(routes, connection.id, row.event)?.deliver_to ?? 'none';
        return (
          <div key={row.event} className="niuu:contents">
            <span className="niuu:text-sm niuu:text-text-primary">{row.label}</span>
            <select
              aria-label={`Send ${row.label} to`}
              className={CONTROL}
              value={current}
              disabled={!manage || setRoute.isPending}
              onChange={(event) => choose(row.event, event.target.value as Destination)}
            >
              {row.destinations.map((destination) => (
                <option key={destination} value={destination}>
                  {destination === 'channel'
                    ? channelLabel(connection)
                    : destination === 'recipient'
                      ? row.recipient
                      : 'Nobody'}
                </option>
              ))}
            </select>
          </div>
        );
      })}
    </div>
  );
}

function ConnectionRow({
  project,
  connection,
  entry,
  members,
  routes,
  manage,
}: {
  project: ForgeProject;
  connection: TeamIntegration;
  entry: TeamCatalogEntry | undefined;
  members: readonly ProjectMember[];
  routes: readonly TeamRoute[];
  manage: boolean;
}) {
  const remove = useRemoveTeamIntegration(project);
  const [routeError, setRouteError] = useState<unknown>(null);
  const name = entry?.name ?? connection.slug;
  const error = remove.error ?? routeError;

  return (
    <li
      className="niuu:flex niuu:flex-col niuu:gap-2 niuu:border-t niuu:border-border-subtle niuu:py-3"
      data-testid={`project-integration-${connection.id}`}
    >
      <div className="niuu:flex niuu:items-center niuu:gap-3">
        <span
          aria-hidden="true"
          className="niuu:flex niuu:h-7 niuu:w-7 niuu:shrink-0 niuu:items-center niuu:justify-center niuu:rounded-md niuu:bg-brand/15 niuu:text-[11px] niuu:font-semibold niuu:text-brand-300"
        >
          {name.slice(0, 1).toUpperCase()}
        </span>
        <div className="niuu:flex niuu:min-w-0 niuu:flex-1 niuu:flex-col">
          <span className="niuu:flex niuu:items-center niuu:gap-2 niuu:text-sm niuu:text-text-primary">
            {name}
            <span className="niuu:rounded-full niuu:bg-bg-tertiary niuu:px-2 niuu:py-0.5 niuu:text-[11px] niuu:text-text-secondary">
              {connection.enabled ? 'Connected' : 'Paused'}
            </span>
          </span>
          <span className="niuu:truncate niuu:text-xs niuu:text-text-secondary">
            {subLine(connection, entry, members)}
          </span>
        </div>
        {manage ? (
          <button
            type="button"
            className={BUTTON}
            aria-label={`Remove ${name}`}
            disabled={remove.isPending}
            onClick={() => remove.mutate(connection.id)}
          >
            Remove
          </button>
        ) : null}
      </div>
      {connection.integration_type === MESSAGING ? (
        <RouteGrid
          project={project}
          connection={connection}
          routes={routes}
          manage={manage}
          onError={setRouteError}
        />
      ) : null}
      {error ? (
        <span role="alert" className="niuu:pl-10 niuu:text-xs niuu:text-critical-fg">
          {errorDetail(error)}
        </span>
      ) : null}
    </li>
  );
}

function RecentUse({
  project,
  members,
}: {
  project: ForgeProject;
  members: readonly ProjectMember[];
}) {
  const usage = useTeamUsage(project);
  const uses = usage.data?.pages.flatMap((page) => page.items) ?? [];

  return (
    <div className="niuu:flex niuu:flex-col niuu:gap-1" data-testid="project-integration-usage">
      <span className="niuu:text-[11px] niuu:uppercase niuu:text-text-muted">Recent use</span>
      {usage.error ? (
        <span role="alert" className="niuu:text-xs niuu:text-critical-fg">
          {errorDetail(usage.error)}
        </span>
      ) : null}
      {usage.isSuccess && uses.length === 0 ? (
        <span className="niuu:text-xs niuu:text-text-muted">Nothing has used these yet.</span>
      ) : null}
      <ul className="niuu:m-0 niuu:list-none niuu:p-0">
        {uses.map((use, index) => (
          <li
            key={`${use.connection_id}-${use.occurred_at}-${index}`}
            className="niuu:flex niuu:items-baseline niuu:gap-2 niuu:py-1 niuu:text-xs"
          >
            <span className="niuu:min-w-0 niuu:flex-1 niuu:truncate niuu:text-text-primary">
              {use.subject || use.action}
            </span>
            <span className="niuu:text-text-secondary">
              {use.acting_for ? personName(members, use.acting_for) : use.actor}
            </span>
            <span className="niuu:text-text-muted">
              {use.occurred_at ? relTime(use.occurred_at) : ''}
            </span>
          </li>
        ))}
      </ul>
      {usage.hasNextPage ? (
        <button
          type="button"
          className={`${BUTTON} niuu:self-start`}
          disabled={usage.isFetchingNextPage}
          onClick={() => void usage.fetchNextPage()}
        >
          Show older
        </button>
      ) : null}
    </div>
  );
}

/** What the team has connected, where its messages go, and what has used it. */
export function ProjectIntegrations({ project }: { project: ForgeProject }) {
  const members = useProjectMembers(project);
  const viewer = useViewer();
  const catalog = useTeamCatalog(project);
  const connections = useTeamIntegrations(project);
  const routes = useTeamRoutes(project);
  const [connecting, setConnecting] = useState(false);
  const memberList = members.data ?? [];
  const manage =
    canManageMembers(memberList, viewer.data) &&
    !isForbidden(connections.error ?? routes.error ?? catalog.error);
  const loadError = connections.error ?? routes.error ?? catalog.error;
  const list = connections.data ?? [];

  return (
    <SectionCard
      title="Team integrations"
      description="Used by everyone on this project and by agents working for them. They act as the team's bot, never as a person."
      actions={
        manage ? (
          <button
            type="button"
            className={BUTTON}
            disabled={!catalog.data}
            onClick={() => setConnecting((open) => !open)}
          >
            Connect
          </button>
        ) : null
      }
    >
      <div className="niuu:flex niuu:flex-col niuu:gap-3" data-testid="project-integrations">
        {manage && connecting && catalog.data ? (
          <ConnectIntegration
            project={project}
            catalog={catalog.data}
            onClose={() => setConnecting(false)}
          />
        ) : null}
        {loadError && !isForbidden(loadError) ? (
          <span role="alert" className="niuu:text-xs niuu:text-critical-fg">
            {errorDetail(loadError)}
          </span>
        ) : null}
        {connections.isSuccess && list.length === 0 ? (
          <span className="niuu:text-xs niuu:text-text-muted">Nothing is connected yet.</span>
        ) : null}
        <ul className="niuu:m-0 niuu:list-none niuu:p-0">
          {list.map((connection) => (
            <ConnectionRow
              key={connection.id}
              project={project}
              connection={connection}
              entry={catalog.data?.find((candidate) => candidate.slug === connection.slug)}
              members={memberList}
              routes={routes.data ?? []}
              manage={manage}
            />
          ))}
        </ul>
        {list.length > 0 ? <RecentUse project={project} members={memberList} /> : null}
        {manage ? null : (
          <p className="niuu:m-0 niuu:text-xs niuu:text-text-muted">
            Only owners connect, route or remove.
          </p>
        )}
      </div>
    </SectionCard>
  );
}
