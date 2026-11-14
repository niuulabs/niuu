import { useState } from 'react';
import { Plus, User, X } from 'lucide-react';
import { Field, authorInitials, cn } from '@niuulabs/ui';
import type { PersonaSummary } from '@niuulabs/domain';
import type { ParticipantCandidate, SessionTeamTaskTo } from '../models/volundr.model';
import { CandidatePicker, nameOf } from './CandidatePicker';
import { getDefinitionRune } from './launchWizardModel';
import type { EngineOption } from './launchEngines';
import { MemberAvatar, memberColor } from './MemberAvatar';
import {
  addTeamAgent,
  addTeamInvitee,
  removeTeamInvitee,
  type TeamInvitee,
  engineOf,
  leadTeamAgent,
  patchTeamAgent,
  removeTeamAgent,
  teamAgentLabel,
  type RoomLine,
  type TeamAgentDraft,
} from './teamLaunch';
import './NewTeam.css';

const SELECT =
  'niuu:min-w-0 niuu:flex-1 niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:px-3 niuu:py-2 niuu:text-sm niuu:text-text-primary';
const GHOST_BTN =
  'niuu:inline-flex niuu:items-center niuu:gap-1.5 niuu:rounded-full niuu:border niuu:border-dashed niuu:border-border niuu:px-3 niuu:py-1.5 niuu:text-xs niuu:text-text-secondary niuu:transition-colors niuu:hover:border-brand niuu:hover:text-text-primary';

/** The id of the next agent row: unique among the rows the team has had. */
function nextAgentId(agents: readonly TeamAgentDraft[]): string {
  const taken = agents
    .map((agent) => Number(agent.id.replace('agent-', '')))
    .filter((value) => Number.isInteger(value));
  return `agent-${Math.max(0, ...taken) + 1}`;
}

/**
 * "Who's in it": a row per agent with its persona, its engine and whether it leads, and the
 * way to add another. Exactly one agent leads, and a team keeps at least one agent.
 */
export function TeamMembersEditor({
  agents,
  engines,
  personas,
  onChange,
  invitees,
  onInviteesChange,
  inviteSearch,
}: {
  agents: readonly TeamAgentDraft[];
  engines: readonly EngineOption[];
  /** Null when the persona catalog is not registered: the rows then have no persona. */
  personas: readonly PersonaSummary[] | null;
  onChange: (agents: TeamAgentDraft[]) => void;
  /** The people invited into the room, after the agents. */
  invitees: readonly TeamInvitee[];
  onInviteesChange: (invitees: TeamInvitee[]) => void;
  /** Where the people to invite are found: on the Forge the team will start on. */
  inviteSearch: {
    queryKey: readonly unknown[];
    fetcher: (query: string, signal?: AbortSignal) => Promise<ParticipantCandidate[]>;
  };
}) {
  const [inviting, setInviting] = useState(false);
  const [query, setQuery] = useState('');
  return (
    <div className="niuu:flex niuu:flex-col niuu:gap-3" data-testid="new-team-members">
      <span className="niuu:text-xs niuu:font-semibold niuu:text-text-primary">
        Who&apos;s in it
      </span>
      <ul className="niuu:m-0 niuu:flex niuu:list-none niuu:flex-col niuu:gap-2 niuu:p-0">
        {agents.map((agent, index) => {
          const engine = engineOf(agent, engines);
          return (
            <li
              key={agent.id}
              data-testid={`new-team-agent-${agent.id}`}
              className="niuu:flex niuu:flex-wrap niuu:items-center niuu:gap-2 niuu:rounded-xl niuu:border niuu:border-border-subtle niuu:bg-bg-primary niuu:p-2.5 sm:niuu:flex-nowrap"
            >
              <MemberAvatar
                kind="agent"
                glyph={getDefinitionRune(engine?.definition.key ?? '')}
                color={memberColor(index)}
              />
              {personas ? (
                <select
                  aria-label={`Persona of agent ${index + 1}`}
                  value={agent.personaName}
                  onChange={(event) =>
                    onChange(patchTeamAgent(agents, agent.id, { personaName: event.target.value }))
                  }
                  className={SELECT}
                  data-testid={`new-team-persona-${agent.id}`}
                >
                  <option value="">No persona</option>
                  {personas.map((persona) => (
                    <option key={persona.name} value={persona.name}>
                      {persona.name} · {persona.role}
                    </option>
                  ))}
                </select>
              ) : (
                <span className="niuu:min-w-0 niuu:flex-1 niuu:text-sm niuu:text-text-secondary">
                  Agent {index + 1}
                </span>
              )}
              <select
                aria-label={`Engine of agent ${index + 1}`}
                value={engine?.definition.key ?? ''}
                onChange={(event) =>
                  onChange(patchTeamAgent(agents, agent.id, { definitionKey: event.target.value }))
                }
                className={SELECT}
                data-testid={`new-team-engine-${agent.id}`}
              >
                {engines.map(({ definition }) => (
                  <option key={definition.key} value={definition.key}>
                    {definition.displayName}
                  </option>
                ))}
              </select>
              <button
                type="button"
                aria-pressed={agent.lead}
                onClick={() => onChange(leadTeamAgent(agents, agent.id))}
                data-testid={`new-team-lead-${agent.id}`}
                className={cn(
                  'niuu:rounded-full niuu:border niuu:px-3 niuu:py-1 niuu:text-xs niuu:font-medium niuu:transition-colors',
                  agent.lead
                    ? 'niuu:border-brand niuu:bg-brand/15 niuu:text-brand-300'
                    : 'niuu:border-border-subtle niuu:text-text-muted niuu:hover:text-text-primary',
                )}
              >
                {agent.lead ? 'Leads' : 'Make lead'}
              </button>
              <button
                type="button"
                aria-label={`Remove agent ${index + 1}`}
                disabled={agents.length === 1}
                onClick={() => onChange(removeTeamAgent(agents, agent.id))}
                data-testid={`new-team-remove-${agent.id}`}
                className="niuu:rounded-md niuu:p-1 niuu:text-text-muted niuu:transition-colors niuu:hover:text-text-primary niuu:disabled:cursor-not-allowed niuu:disabled:opacity-40"
              >
                <X className="niuu:h-3.5 niuu:w-3.5" aria-hidden="true" />
              </button>
            </li>
          );
        })}
      </ul>
      {invitees.length > 0 ? (
        <ul className="niuu:m-0 niuu:flex niuu:list-none niuu:flex-col niuu:gap-2 niuu:p-0">
          {invitees.map((person, index) => (
            <li
              key={person.userId}
              data-testid={`new-team-person-${person.userId}`}
              className="niuu:flex niuu:items-center niuu:gap-2 niuu:rounded-xl niuu:border niuu:border-border-subtle niuu:bg-bg-primary niuu:p-2.5"
            >
              <MemberAvatar
                kind="person"
                glyph={authorInitials(person)}
                color={memberColor(agents.length + index)}
              />
              <span className="niuu:flex niuu:min-w-0 niuu:flex-1 niuu:flex-col">
                <span className="niuu:truncate niuu:text-sm niuu:text-text-primary">
                  {nameOf(person)}
                </span>
                <span className="niuu:text-xs niuu:text-text-muted">
                  joins the room · can message anyone
                </span>
              </span>
              <button
                type="button"
                aria-label={`Remove ${nameOf(person)}`}
                onClick={() => onInviteesChange(removeTeamInvitee(invitees, person.userId))}
                data-testid={`new-team-remove-person-${person.userId}`}
                className="niuu:rounded-md niuu:p-1 niuu:text-text-muted niuu:transition-colors niuu:hover:text-text-primary"
              >
                <X className="niuu:h-3.5 niuu:w-3.5" aria-hidden="true" />
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      {inviting ? (
        <div
          className="niuu:flex niuu:flex-col niuu:gap-2 niuu:rounded-xl niuu:border niuu:border-border-subtle niuu:bg-bg-primary niuu:p-2.5"
          data-testid="new-team-invite"
        >
          <CandidatePicker
            queryKey={inviteSearch.queryKey}
            fetcher={inviteSearch.fetcher}
            query={query}
            onQueryChange={setQuery}
            chosen={null}
            exclude={new Set(invitees.map((person) => person.userId))}
            onChoose={(person) => {
              onInviteesChange(addTeamInvitee(invitees, person));
              setQuery('');
              setInviting(false);
            }}
          >
            <button
              type="button"
              onClick={() => {
                setQuery('');
                setInviting(false);
              }}
              className="niuu:rounded-md niuu:px-2 niuu:text-xs niuu:text-text-muted niuu:hover:text-text-primary"
            >
              Cancel
            </button>
          </CandidatePicker>
        </div>
      ) : null}
      <div className="niuu:flex niuu:flex-wrap niuu:gap-2">
        <button
          type="button"
          onClick={() => onChange(addTeamAgent(agents, nextAgentId(agents)))}
          className={GHOST_BTN}
          data-testid="new-team-add-agent"
        >
          <Plus className="niuu:h-3.5 niuu:w-3.5" aria-hidden="true" />
          Add an agent
        </button>
        <button
          type="button"
          onClick={() => setInviting(true)}
          disabled={inviting}
          className={cn(GHOST_BTN, 'niuu:disabled:opacity-40')}
          data-testid="new-team-add-person"
        >
          <User className="niuu:h-3.5 niuu:w-3.5" aria-hidden="true" />
          Invite a person
        </button>
      </div>
    </div>
  );
}

const TASK_TO_OPTIONS: ReadonlyArray<{
  value: SessionTeamTaskTo;
  label: string;
  hint: string;
}> = [
  {
    value: 'lead',
    label: 'The lead',
    hint: 'The lead reads the task, splits it up and hands work to the others in the room.',
  },
  {
    value: 'everyone',
    label: 'Everyone',
    hint: 'Every agent reads the task and they agree in the room who does what.',
  },
];

/** "Who gets the task": shown only when a task was typed. */
export function TaskToPicker({
  value,
  onChange,
}: {
  value: SessionTeamTaskTo;
  onChange: (value: SessionTeamTaskTo) => void;
}) {
  return (
    <Field label="Who gets the task">
      <div
        role="radiogroup"
        aria-label="Who gets the task"
        className="niuu:grid niuu:grid-cols-1 niuu:gap-3 niuu:sm:grid-cols-2"
        data-testid="new-team-task-to"
      >
        {TASK_TO_OPTIONS.map((option) => {
          const active = option.value === value;
          return (
            <button
              key={option.value}
              type="button"
              role="radio"
              aria-checked={active}
              onClick={() => onChange(option.value)}
              data-testid={`new-team-task-to-${option.value}`}
              className={cn(
                'niuu:flex niuu:flex-col niuu:gap-1 niuu:rounded-xl niuu:border niuu:p-3 niuu:text-left niuu:transition-colors',
                active
                  ? 'niuu:border-brand niuu:bg-bg-tertiary'
                  : 'niuu:border-border-subtle niuu:hover:border-brand/40',
              )}
            >
              <span className="niuu:text-sm niuu:font-medium niuu:text-text-primary">
                {option.label}
              </span>
              <span className="niuu:text-xs niuu:text-text-secondary">{option.hint}</span>
            </button>
          );
        })}
      </div>
    </Field>
  );
}

/** "The room": who joins, and what the first moments look like. */
export function TeamRoomPreview({
  agents,
  engines,
  invitees,
  lines,
}: {
  agents: readonly TeamAgentDraft[];
  engines: readonly EngineOption[];
  invitees: readonly TeamInvitee[];
  lines: readonly RoomLine[];
}) {
  return (
    <section className="niuu:flex niuu:flex-col niuu:gap-3" data-testid="new-team-room">
      <h2 className="niuu:m-0 niuu:text-xs niuu:font-semibold niuu:uppercase niuu:tracking-[0.14em] niuu:text-text-muted">
        The room
      </h2>
      <div className="niuu:flex niuu:flex-col niuu:gap-3 niuu:rounded-xl niuu:border niuu:border-border-subtle niuu:bg-bg-primary niuu:p-3">
        <span className="niuu:text-xs niuu:text-text-secondary">Everyone joins one room</span>
        <div className="niuu:flex niuu:flex-wrap niuu:gap-1.5">
          {agents.map((agent, index) => (
            <MemberAvatar
              key={agent.id}
              kind="agent"
              glyph={getDefinitionRune(engineOf(agent, engines)?.definition.key ?? '')}
              color={memberColor(index)}
              title={teamAgentLabel(agent, engines)}
            />
          ))}
          <MemberAvatar kind="person" glyph="You" color="var(--color-brand)" title="You" />
          {invitees.map((person, index) => (
            <MemberAvatar
              key={person.userId}
              kind="person"
              glyph={authorInitials(person)}
              color={memberColor(agents.length + index)}
              title={nameOf(person)}
            />
          ))}
        </div>
        <ul className="niuu:m-0 niuu:flex niuu:list-none niuu:flex-col niuu:gap-1 niuu:p-0">
          {lines.map((line, index) => (
            <li key={index} className="niuu:text-xs niuu:text-text-secondary">
              {line.prefix}
              {line.agent ? (
                <span
                  className="vol-room-line__agent"
                  style={
                    {
                      '--agent-color': memberColor(line.agent.index),
                    } as React.CSSProperties
                  }
                >
                  {line.agent.label}
                </span>
              ) : null}
              {line.suffix}
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
