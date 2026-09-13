import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { EngineSelect } from './EngineSelect';
import type { EngineOption } from './launchEngines';

const engine = (key: string, displayName: string, description = ''): EngineOption => ({
  definition: {
    key,
    displayName,
    description,
    labels: [],
    defaultModel: '',
    compatibleProviders: [],
  },
  providers: [
    {
      connection: {
        id: `${key}-conn`,
        slug: 'claude-code',
        credentialName: 'claude-code-setup',
        createdAt: '',
        updatedAt: '',
      },
      entry: {
        id: 'claude-code',
        slug: 'claude-code',
        name: 'Claude Code (subscription)',
        description: '',
        integrationType: 'ai_provider',
        modelVendor: 'anthropic',
      },
      vendor: 'anthropic',
    },
  ],
});

describe('EngineSelect', () => {
  it('lists the engines, explains the selected one and links to the providers', () => {
    const onChange = vi.fn();
    render(
      <EngineSelect
        engines={[
          engine('skuldClaude', 'Claude Code', 'The usual choice.'),
          engine('skuldCodex', 'Codex'),
        ]}
        value="skuldClaude"
        onChange={onChange}
        testId="engine"
      />,
    );
    const select = screen.getByTestId('engine');
    expect(
      within(select)
        .getAllByRole('option')
        .map((option) => option.textContent),
    ).toEqual(['Claude Code', 'Codex']);
    // the definition's description is not repeated here; the account line is what matters
    expect(screen.getByTestId('engine-hint')).not.toHaveTextContent('The usual choice.');
    expect(screen.getByTestId('engine-hint')).toHaveTextContent(
      'Uses Claude Code (subscription) · claude-code-setup',
    );
    expect(screen.getByRole('link', { name: 'Manage providers' })).toHaveAttribute(
      'href',
      '/settings/integrations',
    );
    fireEvent.change(select, { target: { value: 'skuldCodex' } });
    expect(onChange).toHaveBeenCalledWith('skuldCodex');
  });

  it('keeps a selection no provider powers visible, disabled and explained', () => {
    render(
      <EngineSelect
        engines={[engine('skuldClaude', 'Claude Code')]}
        value="skuldGrok"
        unavailableName="Grok Build"
        onChange={vi.fn()}
        testId="engine"
      />,
    );
    const orphan = within(screen.getByTestId('engine')).getByRole('option', {
      name: 'Grok Build (no provider connected)',
    });
    expect(orphan).toBeDisabled();
    expect(screen.getByTestId('engine-orphaned')).toHaveTextContent(
      'None of your connected providers powers Grok Build.',
    );
  });

  it('points at the provider settings when nothing is connected', () => {
    render(<EngineSelect engines={[]} value="" onChange={vi.fn()} testId="engine" />);
    expect(screen.getByTestId('engine-empty')).toHaveTextContent(
      'No engine has a connected AI provider yet.',
    );
    expect(screen.getByTestId('engine-manage-providers')).toHaveAttribute(
      'href',
      '/settings/integrations',
    );
    expect(screen.queryByTestId('engine')).not.toBeInTheDocument();
  });

  it('shows the failure when the providers could not be loaded', () => {
    render(
      <EngineSelect
        engines={[]}
        value=""
        onChange={vi.fn()}
        error={new Error('offline')}
        testId="engine"
      />,
    );
    expect(screen.getByTestId('engine-error')).toHaveTextContent(
      'Could not load your providers: offline',
    );
    expect(screen.queryByTestId('engine-empty')).not.toBeInTheDocument();
  });

  it('offers the account when more than one powers the engine', () => {
    const onProviderChange = vi.fn();
    const two = engine('skuldClaude', 'Claude Code');
    two.providers = [
      two.providers[0]!,
      {
        connection: {
          id: 'key-conn',
          slug: 'anthropic',
          credentialName: 'anthropic-work',
          createdAt: '',
          updatedAt: '',
        },
        entry: {
          id: 'anthropic',
          slug: 'anthropic',
          name: 'Anthropic (Claude API)',
          description: '',
          integrationType: 'ai_provider',
          modelVendor: 'anthropic',
        },
        vendor: 'anthropic',
      },
    ];
    render(
      <EngineSelect
        engines={[two]}
        value="skuldClaude"
        onChange={vi.fn()}
        selectedIntegrationIds={['git-1', 'key-conn']}
        onProviderChange={onProviderChange}
        testId="engine"
      />,
    );
    const account = screen.getByTestId('engine-account');
    expect(
      within(account)
        .getAllByRole('option')
        .map((option) => option.textContent),
    ).toEqual([
      'Claude Code (subscription) · claude-code-setup',
      'Anthropic (Claude API) · anthropic-work',
    ]);
    expect(account).toHaveValue('key-conn');
    expect(screen.getByTestId('engine-hint')).toHaveTextContent(
      'Uses Anthropic (Claude API) · anthropic-work',
    );
    fireEvent.change(account, { target: { value: 'skuldClaude-conn' } });
    expect(onProviderChange).toHaveBeenCalledWith('skuldClaude-conn');
  });

  it('shows no account picker for a single provider', () => {
    render(
      <EngineSelect
        engines={[engine('skuldClaude', 'Claude Code')]}
        value="skuldClaude"
        onChange={vi.fn()}
        testId="engine"
      />,
    );
    expect(screen.queryByTestId('engine-account')).not.toBeInTheDocument();
  });

  it('waits while providers load instead of claiming none is connected', () => {
    render(<EngineSelect engines={[]} value="" onChange={vi.fn()} loading testId="engine" />);
    expect(screen.queryByTestId('engine-empty')).not.toBeInTheDocument();
    expect(screen.getByTestId('engine')).toBeDisabled();
  });
});
