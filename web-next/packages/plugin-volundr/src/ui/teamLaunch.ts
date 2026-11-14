import type { RepoRecord } from '@niuulabs/ui';
import type {
  IntegrationConnection,
  SessionSource,
  SessionTeamTaskTo,
  StartTeamRequest,
} from '../models/volundr.model';
import {
  launchModel,
  quickLaunchIntegrationIds,
  selectedEngineProvider,
  type EngineOption,
} from './launchEngines';

/**
 * What the "A team" tab collects before a team starts: the agents in the room. Everything a
 * team shares with a single session (task, repository, branch) is the page's own state.
 */
export interface TeamAgentDraft {
  /** Stable across edits, so a row keeps its identity while its persona or engine changes. */
  id: string;
  /** The persona the agent works as; empty is no persona. */
  personaName: string;
  /** The engine's definition key; empty is the first engine this person can run. */
  definitionKey: string;
  lead: boolean;
}

/** A person invited into the team's room: they are granted in once the team starts. */
export interface TeamInvitee {
  userId: string;
  displayName: string;
}

export function addTeamInvitee(
  invitees: readonly TeamInvitee[],
  invitee: TeamInvitee,
): TeamInvitee[] {
  if (invitees.some((person) => person.userId === invitee.userId)) return [...invitees];
  return [...invitees, invitee];
}

export function removeTeamInvitee(invitees: readonly TeamInvitee[], userId: string): TeamInvitee[] {
  return invitees.filter((person) => person.userId !== userId);
}

export function newTeamAgent(id: string, lead = false): TeamAgentDraft {
  return { id, personaName: '', definitionKey: '', lead };
}

/** The agents a team starts from: a lead and one more. */
export function initialTeamAgents(): TeamAgentDraft[] {
  return [newTeamAgent('agent-1', true), newTeamAgent('agent-2')];
}

export function addTeamAgent(agents: readonly TeamAgentDraft[], id: string): TeamAgentDraft[] {
  return [...agents, newTeamAgent(id, agents.length === 0)];
}

/** Without an agent that leads, the first one that remains does: a team always has a lead. */
export function removeTeamAgent(agents: readonly TeamAgentDraft[], id: string): TeamAgentDraft[] {
  const remaining = agents.filter((agent) => agent.id !== id);
  if (remaining.length === 0 || remaining.some((agent) => agent.lead)) return remaining;
  return remaining.map((agent, index) => (index === 0 ? { ...agent, lead: true } : agent));
}

export function leadTeamAgent(agents: readonly TeamAgentDraft[], id: string): TeamAgentDraft[] {
  return agents.map((agent) => ({ ...agent, lead: agent.id === id }));
}

export function patchTeamAgent(
  agents: readonly TeamAgentDraft[],
  id: string,
  patch: Partial<Pick<TeamAgentDraft, 'personaName' | 'definitionKey'>>,
): TeamAgentDraft[] {
  return agents.map((agent) => (agent.id === id ? { ...agent, ...patch } : agent));
}

/** The engine an agent runs on: its own pick, else the first one this person can run. */
export function engineOf(
  agent: TeamAgentDraft,
  engines: readonly EngineOption[],
): EngineOption | undefined {
  return engines.find((engine) => engine.definition.key === agent.definitionKey) ?? engines[0];
}

/** What the room calls an agent: its persona, else its engine. */
export function teamAgentLabel(agent: TeamAgentDraft, engines: readonly EngineOption[]): string {
  if (agent.personaName) return agent.personaName;
  return engineOf(agent, engines)?.definition.displayName ?? 'Agent';
}

/** "Start session" for one agent, "Start 3 sessions" for three. */
export function teamStartLabel(agentCount: number): string {
  return agentCount === 1 ? 'Start session' : `Start ${agentCount} sessions`;
}

/** One line of the room preview: a name inside a sentence, when the line names an agent. */
export interface RoomLine {
  prefix: string;
  /** The agent the line names, by its place in the team (which fixes its colour). */
  agent?: { index: number; label: string };
  suffix: string;
}

/** Who the room preview says gets the task, and who can talk to everyone. */
export function roomPreviewLines(input: {
  agents: readonly TeamAgentDraft[];
  engines: readonly EngineOption[];
  hasTask: boolean;
  taskTo: SessionTeamTaskTo;
  invitees: readonly TeamInvitee[];
}): RoomLine[] {
  const { agents, engines, hasTask, taskTo, invitees } = input;
  const lines: RoomLine[] = [];
  const lead = agents.find((agent) => agent.lead);
  const named = (agent: TeamAgentDraft) => ({
    index: agents.indexOf(agent),
    label: teamAgentLabel(agent, engines),
  });
  if (!hasTask) {
    lines.push({ prefix: `All ${agents.length} agents start and wait`, suffix: '' });
    lines.push({ prefix: 'The first message in the room gets them going', suffix: '' });
  } else if (taskTo === 'lead' && lead) {
    lines.push({ prefix: '', agent: named(lead), suffix: ' gets the task' });
    for (const agent of agents) {
      if (agent === lead) continue;
      lines.push({ prefix: '↳ hands work to ', agent: named(agent), suffix: '' });
    }
  } else {
    lines.push({ prefix: `All ${agents.length} agents get the task`, suffix: '' });
  }
  const who = ['You', ...invitees.map((person) => person.displayName || person.userId)];
  lines.push({
    prefix: `${who.length > 1 ? `${who.slice(0, -1).join(', ')} and ${who.at(-1)}` : 'You'} can talk to everyone`,
    suffix: '',
  });
  return lines;
}

/** The "What happens next" steps of a team. */
export function teamNextSteps(input: {
  agentCount: number;
  leadLabel: string | null;
  hasTask: boolean;
  taskTo: SessionTeamTaskTo;
}): string[] {
  const { agentCount, leadLabel, hasTask, taskTo } = input;
  const boot =
    agentCount === 1
      ? 'A sandbox boots on the branch for the agent.'
      : `${agentCount} sandboxes boot on the same branch, one per agent.`;
  let task = 'Each agent reads the task and they agree who does what.';
  if (!hasTask) task = 'They wait until you or someone in the room sends the first message.';
  else if (taskTo === 'lead')
    task = `${leadLabel ?? 'The lead'} reads the task and hands out the work.`;
  return [
    boot,
    'They join one room and see each other’s messages, like a workflow step.',
    task,
    'You see them all under one session and can message one or all.',
  ];
}

/**
 * The request that starts the team. A task and who gets it go together: with no task nobody
 * gets one and the agents wait in the room for the first message.
 */
export function buildStartTeamRequest(input: {
  name: string;
  source: SessionSource;
  task: string;
  taskTo: SessionTeamTaskTo;
  agents: readonly TeamAgentDraft[];
  engines: readonly EngineOption[];
  invitees: readonly TeamInvitee[];
  /** The Forge to start on: the page's selected target, as a single launch sends it. */
  instanceId?: string;
  integrations: IntegrationConnection[];
  repos: readonly RepoRecord[];
  repoUrl: string;
}): StartTeamRequest {
  const task = input.task.trim();
  const integrationIds = new Set<string>();
  const members = input.agents.map((agent) => {
    const engine = engineOf(agent, input.engines);
    if (!engine) {
      throw new Error(
        'No session engine is available for this agent. Connect an AI provider that can run one.',
      );
    }
    const provider = selectedEngineProvider(engine, []);
    for (const id of quickLaunchIntegrationIds({
      provider,
      integrations: input.integrations,
      repos: input.repos,
      repoUrl: input.repoUrl,
      local: false,
    })) {
      integrationIds.add(id);
    }
    return {
      model: launchModel(engine, provider, ''),
      definition: engine.definition.key,
      personaName: agent.personaName,
      lead: agent.lead,
    };
  });
  return {
    name: input.name,
    source: input.source,
    ...(task ? { task, taskTo: input.taskTo } : {}),
    members,
    ...(input.invitees.length > 0
      ? { invitees: input.invitees.map((person) => person.userId) }
      : {}),
    integrationIds: [...integrationIds],
    terminalRestricted: false,
    instanceId: input.instanceId,
  };
}
