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
    expect(screen.getByTestId('engine-hint')).toHaveTextContent('The usual choice.');
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

  it('waits while providers load instead of claiming none is connected', () => {
    render(<EngineSelect engines={[]} value="" onChange={vi.fn()} loading testId="engine" />);
    expect(screen.queryByTestId('engine-empty')).not.toBeInTheDocument();
    expect(screen.getByTestId('engine')).toBeDisabled();
  });
});
