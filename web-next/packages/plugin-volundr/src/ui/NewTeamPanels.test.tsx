import { describe, expect, it } from 'vitest';
import { useState } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import type { IntegrationConnection, SessionDefinition } from '../models/volundr.model';
import type { ConnectedProvider, EngineOption } from './launchEngines';
import { TeamMembersEditor } from './NewTeamPanels';
import { initialTeamAgents, modelOf, type TeamAgentDraft } from './teamLaunch';

function provider(id: string, name: string, models?: string[]): ConnectedProvider {
  return {
    connection: {
      id,
      slug: id,
      enabled: true,
      integrationType: 'ai_provider',
      ...(models ? { config: { models } } : {}),
    } as unknown as IntegrationConnection,
    entry: { slug: id, name, integrationType: 'ai_provider' } as never,
    vendor: 'x',
  };
}

function engine(key: string, name: string, defaultModel: string, providers: ConnectedProvider[]) {
  return {
    definition: { key, displayName: name, defaultModel } as unknown as SessionDefinition,
    providers,
  } satisfies EngineOption;
}

const claude = provider('claude', 'Claude Code');
const server = provider('server', 'Model server', ['llama3.2:latest', 'qwen3:8b']);
const claudeEngine = engine('skuldClaude', 'Claude Code', 'claude-opus', [claude, server]);
const codexEngine = engine('skuldCodex', 'Codex', 'gpt-codex', [provider('openai', 'OpenAI')]);

let latest: TeamAgentDraft[] = initialTeamAgents();

function Harness({ engines }: { engines: EngineOption[] }) {
  const [agents, setAgents] = useState<TeamAgentDraft[]>(initialTeamAgents());
  return (
    <TeamMembersEditor
      agents={agents}
      engines={engines}
      personas={null}
      onChange={(next) => {
        latest = next;
        setAgents(next);
      }}
      invitees={[]}
      onInviteesChange={() => {}}
      inviteSearch={{ queryKey: ['x'], fetcher: async () => [] }}
    />
  );
}

describe('TeamMembersEditor provider and model', () => {
  it('offers no provider choice when one provider runs the engine', () => {
    latest = initialTeamAgents();
    render(<Harness engines={[codexEngine]} />);
    expect(screen.getByTestId('new-team-engine-agent-1')).toBeInTheDocument();
    expect(screen.queryByTestId('new-team-engine-agent-1-account')).not.toBeInTheDocument();
    expect(screen.queryByTestId('new-team-engine-agent-1-model')).not.toBeInTheDocument();
  });

  it('offers the providers when two run the engine, and the served models of the chosen one', () => {
    latest = initialTeamAgents();
    render(<Harness engines={[claudeEngine, codexEngine]} />);
    const account = screen.getByLabelText('Account of agent 2') as HTMLSelectElement;
    expect(account.value).toBe('claude');
    expect(screen.queryByLabelText('Model of agent 2')).not.toBeInTheDocument();
    expect(modelOf(latest[1]!, claudeEngine)).toBe('claude-opus');

    fireEvent.change(account, { target: { value: 'server' } });
    const model = screen.getByLabelText('Model of agent 2') as HTMLSelectElement;
    expect(model.value).toBe('llama3.2:latest');
    fireEvent.change(model, { target: { value: 'qwen3:8b' } });
    expect(latest[1]).toMatchObject({ providerId: 'server', model: 'qwen3:8b' });
    expect(modelOf(latest[1]!, claudeEngine)).toBe('qwen3:8b');
    // the other agent is untouched
    expect(latest[0]).toMatchObject({ providerId: '', model: '' });
  });

  it('resets the provider and model when the engine changes, and when the provider does', () => {
    latest = initialTeamAgents();
    render(<Harness engines={[claudeEngine, codexEngine]} />);
    fireEvent.change(screen.getByLabelText('Account of agent 2'), { target: { value: 'server' } });
    fireEvent.change(screen.getByLabelText('Model of agent 2'), { target: { value: 'qwen3:8b' } });

    // another provider serves its own models: the old pick is dropped
    fireEvent.change(screen.getByLabelText('Account of agent 2'), { target: { value: 'claude' } });
    expect(latest[1]).toMatchObject({ providerId: 'claude', model: '' });

    fireEvent.change(screen.getByLabelText('Account of agent 2'), { target: { value: 'server' } });
    fireEvent.change(screen.getByLabelText('Model of agent 2'), { target: { value: 'qwen3:8b' } });
    fireEvent.change(screen.getByLabelText('Engine of agent 2'), {
      target: { value: 'skuldCodex' },
    });
    expect(latest[1]).toMatchObject({ definitionKey: 'skuldCodex', providerId: '', model: '' });
    expect(screen.queryByLabelText('Account of agent 2')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Model of agent 2')).not.toBeInTheDocument();
  });
});
