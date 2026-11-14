import { describe, it, expect } from 'vitest';
import type { IntegrationConnection, SessionDefinition } from '../models/volundr.model';
import type { EngineOption } from './launchEngines';
import {
  addTeamAgent,
  addTeamInvitee,
  removeTeamInvitee,
  buildStartTeamRequest,
  engineOf,
  initialTeamAgents,
  leadTeamAgent,
  modelOf,
  newTeamAgent,
  patchTeamAgent,
  providerOf,
  removeTeamAgent,
  roomPreviewLines,
  teamAgentLabel,
  teamNextSteps,
  teamStartLabel,
} from './teamLaunch';

function definition(key: string, displayName: string, defaultModel: string): SessionDefinition {
  return {
    key,
    displayName,
    defaultModel,
    compatibleProviders: [],
    labels: [],
  } as unknown as SessionDefinition;
}

function connection(id: string, integrationType: string): IntegrationConnection {
  return { id, integrationType, enabled: true } as unknown as IntegrationConnection;
}

const claude: EngineOption = {
  definition: definition('skuldClaude', 'Claude Code', 'claude-sonnet'),
  providers: [
    {
      connection: connection('ai-claude', 'ai_provider'),
      entry: { slug: 'claude', integrationType: 'ai_provider' } as never,
      vendor: 'anthropic',
    },
  ],
};
const codex: EngineOption = {
  definition: definition('skuldCodex', 'Codex', 'gpt-codex'),
  providers: [
    {
      connection: connection('ai-openai', 'ai_provider'),
      entry: { slug: 'openai', integrationType: 'ai_provider' } as never,
      vendor: 'openai',
    },
  ],
};
const engines = [claude, codex];

describe('the agents of a team being set up', () => {
  it('starts with a lead and one more', () => {
    const agents = initialTeamAgents();
    expect(agents.map((agent) => [agent.id, agent.lead])).toEqual([
      ['agent-1', true],
      ['agent-2', false],
    ]);
  });

  it('adds an agent that does not lead, or leads when it is the only one', () => {
    expect(addTeamAgent(initialTeamAgents(), 'agent-3').at(-1)?.lead).toBe(false);
    expect(addTeamAgent([], 'agent-1')).toEqual([newTeamAgent('agent-1', true)]);
  });

  it('hands the lead to the first remaining agent when the lead is removed', () => {
    const [lead, second] = initialTeamAgents();
    const left = removeTeamAgent([lead!, second!, newTeamAgent('agent-3')], 'agent-1');
    expect(left.map((agent) => [agent.id, agent.lead])).toEqual([
      ['agent-2', true],
      ['agent-3', false],
    ]);
    expect(removeTeamAgent([lead!, second!], 'agent-2')).toEqual([lead]);
    expect(removeTeamAgent([lead!], 'agent-1')).toEqual([]);
  });

  it('makes exactly one agent lead', () => {
    const agents = leadTeamAgent(initialTeamAgents(), 'agent-2');
    expect(agents.map((agent) => agent.lead)).toEqual([false, true]);
  });

  it('changes one agent and leaves the others', () => {
    const agents = patchTeamAgent(initialTeamAgents(), 'agent-2', {
      personaName: 'reviewer',
      definitionKey: 'skuldCodex',
    });
    expect(agents[1]).toMatchObject({ personaName: 'reviewer', definitionKey: 'skuldCodex' });
    expect(agents[0]).toMatchObject({
      personaName: '',
      definitionKey: '',
      providerId: '',
      model: '',
    });
  });

  it('runs an agent on its pick, else on the first engine', () => {
    const [first, second] = patchTeamAgent(initialTeamAgents(), 'agent-2', {
      definitionKey: 'skuldCodex',
    });
    expect(engineOf(first!, engines)).toBe(claude);
    expect(engineOf(second!, engines)).toBe(codex);
    expect(engineOf(first!, [])).toBeUndefined();
  });

  it('names an agent by its persona, else its engine, else generically', () => {
    const agent = newTeamAgent('a');
    expect(teamAgentLabel({ ...agent, personaName: 'coder' }, engines)).toBe('coder');
    expect(teamAgentLabel(agent, engines)).toBe('Claude Code');
    expect(teamAgentLabel(agent, [])).toBe('Agent');
  });

  it('labels the start button by how many sessions it starts', () => {
    expect(teamStartLabel(1)).toBe('Start session');
    expect(teamStartLabel(3)).toBe('Start 3 sessions');
  });
});

describe('the room preview', () => {
  const agents = patchTeamAgent(initialTeamAgents(), 'agent-2', { personaName: 'coder' });
  const text = (lines: ReturnType<typeof roomPreviewLines>) =>
    lines.map((line) => `${line.prefix}${line.agent?.label ?? ''}${line.suffix}`);

  it('says they wait when there is no task', () => {
    expect(
      text(roomPreviewLines({ agents, engines, hasTask: false, taskTo: 'lead', invitees: [] })),
    ).toEqual([
      'All 2 agents start and wait',
      'The first message in the room gets them going',
      'You can talk to everyone',
    ]);
  });

  it('names the lead and who it hands work to when the lead gets the task', () => {
    const lines = roomPreviewLines({
      agents,
      engines,
      hasTask: true,
      taskTo: 'lead',
      invitees: [],
    });
    expect(text(lines)).toEqual([
      'Claude Code gets the task',
      '↳ hands work to coder',
      'You can talk to everyone',
    ]);
    expect(lines[0]?.agent?.index).toBe(0);
    expect(lines[1]?.agent?.index).toBe(1);
  });

  it('says everyone gets the task, also when no agent leads', () => {
    expect(
      text(
        roomPreviewLines({ agents, engines, hasTask: true, taskTo: 'everyone', invitees: [] }),
      )[0],
    ).toBe('All 2 agents get the task');
    const leaderless = agents.map((agent) => ({ ...agent, lead: false }));
    expect(
      text(
        roomPreviewLines({
          agents: leaderless,
          engines,
          hasTask: true,
          taskTo: 'lead',
          invitees: [],
        }),
      )[0],
    ).toBe('All 2 agents get the task');
  });
});

describe('the people invited into a team', () => {
  const sam = { userId: 'sam', displayName: 'Sam K.' };
  const alex = { userId: 'alex', displayName: '' };

  it('adds a person once and takes them out again', () => {
    const invited = addTeamInvitee(addTeamInvitee([], sam), sam);
    expect(invited).toEqual([sam]);
    expect(removeTeamInvitee(addTeamInvitee(invited, alex), 'sam')).toEqual([alex]);
  });

  it('says in the room preview who can talk to everyone', () => {
    const agents = initialTeamAgents();
    const last = (invitees: (typeof sam)[]) =>
      roomPreviewLines({ agents, engines, hasTask: false, taskTo: 'lead', invitees }).at(-1)
        ?.prefix;
    expect(last([])).toBe('You can talk to everyone');
    expect(last([sam])).toBe('You and Sam K. can talk to everyone');
    expect(last([sam, alex])).toBe('You, Sam K. and alex can talk to everyone');
  });
});

describe('what happens next for a team', () => {
  it('follows the opening task', () => {
    const base = { agentCount: 3, leadLabel: 'Coordinator' };
    expect(teamNextSteps({ ...base, hasTask: false, taskTo: 'lead' })).toEqual([
      'One workspace boots with your repository checked out; the 3 agents work in it together.',
      'They join one room and see each other’s messages, like a workflow step.',
      'They wait until you or someone in the room sends the first message.',
      'You see them all under one session and can message one or all.',
    ]);
    expect(teamNextSteps({ ...base, hasTask: true, taskTo: 'lead' })[2]).toBe(
      'Coordinator reads the task and hands out the work.',
    );
    expect(teamNextSteps({ ...base, leadLabel: null, hasTask: true, taskTo: 'lead' })[2]).toBe(
      'The lead reads the task and hands out the work.',
    );
    expect(teamNextSteps({ ...base, hasTask: true, taskTo: 'everyone' })[2]).toBe(
      'Each agent reads the task and they agree who does what.',
    );
    expect(teamNextSteps({ ...base, agentCount: 1, hasTask: false, taskTo: 'lead' })[0]).toBe(
      'One workspace boots with your repository checked out for the agent.',
    );
  });
});

describe('the request that starts a team', () => {
  const source = { type: 'git' as const, repo: 'github.com/niuulabs/niuu', branch: 'dev' };
  const agents = patchTeamAgent(initialTeamAgents(), 'agent-2', {
    definitionKey: 'skuldCodex',
    personaName: 'coder',
  });
  const integrations = [
    connection('ai-claude', 'ai_provider'),
    connection('ai-openai', 'ai_provider'),
    connection('git-1', 'source_control'),
    connection('slack', 'messaging'),
  ];
  const input = {
    name: 'niuu',
    source,
    task: '  Fix the refresh bug  ',
    taskTo: 'everyone' as const,
    agents,
    engines,
    invitees: [],
    integrations,
    repos: [],
    repoUrl: 'github.com/niuulabs/niuu',
  };

  it('gives each agent its own engine, model and persona, and the task to who was chosen', () => {
    const request = buildStartTeamRequest(input);
    expect(request.task).toBe('Fix the refresh bug');
    expect(request.taskTo).toBe('everyone');
    expect(request.members).toEqual([
      { model: 'claude-sonnet', definition: 'skuldClaude', personaName: '', lead: true },
      { model: 'gpt-codex', definition: 'skuldCodex', personaName: 'coder', lead: false },
    ]);
    // Every engine's AI account, the Git account and the other integrations travel together.
    expect(new Set(request.integrationIds)).toEqual(
      new Set(['ai-claude', 'ai-openai', 'git-1', 'slack']),
    );
    expect(request.terminalRestricted).toBe(false);
  });

  it('sends the invitees and the Forge it starts on', () => {
    const request = buildStartTeamRequest({
      ...input,
      invitees: [{ userId: 'sam', displayName: 'Sam K.' }],
      instanceId: 'thor',
    });
    expect(request.invitees).toEqual(['sam']);
    expect(request.instanceId).toBe('thor');
    expect(buildStartTeamRequest(input)).not.toHaveProperty('invitees');
  });

  it('sends neither task nor who gets it when the team waits for the first message', () => {
    const request = buildStartTeamRequest({ ...input, task: '   ' });
    expect(request).not.toHaveProperty('task');
    expect(request).not.toHaveProperty('taskTo');
  });

  it('refuses an agent no engine can run', () => {
    expect(() => buildStartTeamRequest({ ...input, engines: [] })).toThrow(
      'No session engine is available',
    );
  });
});

describe('the provider and model of each agent', () => {
  const server = {
    connection: {
      ...connection('ai-server', 'ai_provider'),
      config: { models: ['llama3.2:latest', 'qwen3:8b'] },
    } as IntegrationConnection,
    entry: { slug: 'model-server', integrationType: 'ai_provider' } as never,
    vendor: 'local',
  };
  const twoProviders: EngineOption = {
    definition: definition('skuldClaude', 'Claude Code', 'claude-sonnet'),
    providers: [...claude.providers, server],
  };
  const source = { type: 'git' as const, repo: 'github.com/niuulabs/niuu', branch: 'dev' };
  const input = {
    name: 'niuu',
    source,
    task: '',
    taskTo: 'lead' as const,
    engines: [twoProviders, codex],
    invitees: [],
    integrations: [
      connection('ai-claude', 'ai_provider'),
      server.connection,
      connection('ai-openai', 'ai_provider'),
      connection('git-1', 'source_control'),
    ],
    repos: [],
    repoUrl: 'github.com/niuulabs/niuu',
  };

  it('runs an agent on the engine first provider and its default model until it picks another', () => {
    const [agent] = initialTeamAgents();
    expect(providerOf(agent!, twoProviders)?.connection.id).toBe('ai-claude');
    expect(modelOf(agent!, twoProviders)).toBe('claude-sonnet');
    expect(providerOf(agent!, undefined)).toBeUndefined();
  });

  it('runs an agent on the provider it picked, on its first served model or the one it picked', () => {
    const [agent] = patchTeamAgent(initialTeamAgents(), 'agent-1', { providerId: 'ai-server' });
    expect(providerOf(agent!, twoProviders)?.connection.id).toBe('ai-server');
    expect(modelOf(agent!, twoProviders)).toBe('llama3.2:latest');
    const [picked] = patchTeamAgent([agent!], 'agent-1', { model: 'qwen3:8b' });
    expect(modelOf(picked!, twoProviders)).toBe('qwen3:8b');
    // a pick the provider does not serve is not sent
    const [stale] = patchTeamAgent([agent!], 'agent-1', { model: 'gpt-x' });
    expect(modelOf(stale!, twoProviders)).toBe('llama3.2:latest');
  });

  it('starts over on the first provider and default model when the engine changes', () => {
    const picked = patchTeamAgent(initialTeamAgents(), 'agent-1', {
      providerId: 'ai-server',
      model: 'qwen3:8b',
    });
    const [same] = patchTeamAgent(picked, 'agent-1', { definitionKey: '' });
    expect(same).toMatchObject({ providerId: 'ai-server', model: 'qwen3:8b' });
    const [changed] = patchTeamAgent(picked, 'agent-1', { definitionKey: 'skuldCodex' });
    expect(changed).toMatchObject({ providerId: '', model: '', definitionKey: 'skuldCodex' });
  });

  it('gives each member its own model and attaches the union of the chosen providers', () => {
    const agents = patchTeamAgent(initialTeamAgents(), 'agent-2', {
      providerId: 'ai-server',
      model: 'qwen3:8b',
    });
    const request = buildStartTeamRequest({ ...input, agents });
    expect(request.members.map((member) => member.model)).toEqual(['claude-sonnet', 'qwen3:8b']);
    expect(new Set(request.integrationIds)).toEqual(new Set(['ai-claude', 'ai-server', 'git-1']));
    // the provider nobody chose is not attached
    expect(request.integrationIds).not.toContain('ai-openai');
  });
});
