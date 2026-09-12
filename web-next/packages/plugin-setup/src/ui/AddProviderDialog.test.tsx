import { describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { renderWithSetup } from '../testing/renderWithSetup';
import { createMockSetupService, MOCK_CATALOG } from '../adapters/mock';
import { WIZARD_STEPS, providerGroups } from '../domain/setup';
import { AddProviderDialog, modesFor } from './AddProviderDialog';

const providers = WIZARD_STEPS.find((s) => s.id === 'providers')!;
const git = WIZARD_STEPS.find((s) => s.id === 'git')!;

const base = {
  open: true,
  onOpenChange: vi.fn(),
  noun: 'provider',
  connections: [],
  connectingSlug: null,
  connectErrorSlug: null,
  connectError: null,
  testingId: null,
  testResults: {},
  onConnect: vi.fn(),
  onTest: vi.fn(),
};

describe('modesFor', () => {
  it('puts the usable mode first', () => {
    const groups = providerGroups(MOCK_CATALOG, providers);
    expect(modesFor(groups.find((g) => g.key === 'anthropic')!)).toEqual(['signin', 'key']);
    expect(modesFor(groups.find((g) => g.key === 'deepseek')!)).toEqual(['key']);
    const github = providerGroups(
      MOCK_CATALOG.map((e) => (e.slug === 'github' ? { ...e, signInAvailable: false } : e)),
      git,
    ).find((g) => g.key === 'github')!;
    expect(modesFor(github)).toEqual(['key', 'signin']);
  });
});

describe('AddProviderDialog', () => {
  it('walks provider → method → key form and goes back', () => {
    const onConnect = vi.fn();
    const groups = providerGroups(MOCK_CATALOG, providers);
    renderWithSetup(<AddProviderDialog {...base} groups={groups} onConnect={onConnect} />);
    expect(screen.getByText('Add provider')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('setup-add-pick-anthropic'));
    expect(screen.getByText('Anthropic · Claude: how?')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('setup-add-mode-key'));
    fireEvent.change(screen.getByTestId('setup-input-anthropic-api_key'), {
      target: { value: 'sk-ant' },
    });
    fireEvent.click(screen.getByTestId('setup-connect-anthropic'));
    expect(onConnect).toHaveBeenCalledWith(
      expect.objectContaining({ slug: 'anthropic', credential: { api_key: 'sk-ant' } }),
    );
    fireEvent.click(screen.getByTestId('setup-add-back'));
    expect(screen.getByTestId('setup-add-mode-signin')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('setup-add-back'));
    expect(screen.getByTestId('setup-add-pick-anthropic')).toBeInTheDocument();
  });

  it('skips the method question for single-mode providers and runs a sign-in', async () => {
    const service = createMockSetupService({ latencyMs: 0 });
    const groups = providerGroups(MOCK_CATALOG, providers);
    renderWithSetup(<AddProviderDialog {...base} groups={groups} />, { service });
    fireEvent.click(screen.getByTestId('setup-add-pick-deepseek'));
    expect(screen.getByTestId('setup-input-deepseek-api_key')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('setup-add-back'));
    fireEvent.click(screen.getByTestId('setup-add-pick-openai'));
    fireEvent.click(screen.getByTestId('setup-add-mode-signin'));
    fireEvent.click(screen.getByTestId('setup-signin-start-codex'));
    await waitFor(() => expect(screen.getByTestId('setup-signin-code-codex')).toBeInTheDocument());
  });

  it('explains a sign-in this install cannot run and honours presets', () => {
    const catalog = MOCK_CATALOG.map((e) =>
      e.slug === 'github' ? { ...e, signInAvailable: false } : e,
    );
    const groups = providerGroups(catalog, git);
    renderWithSetup(
      <AddProviderDialog
        {...base}
        noun="Git host"
        groups={groups}
        initialGroupKey="github"
        initialMode="signin"
      />,
    );
    expect(screen.getByTestId('setup-signin-unavailable-github')).toHaveTextContent(
      'oauth.clients.github.client_id',
    );
  });

  it('says when nothing is left to add and resets on close', () => {
    const onOpenChange = vi.fn();
    renderWithSetup(<AddProviderDialog {...base} groups={[]} onOpenChange={onOpenChange} />);
    expect(screen.getByTestId('setup-add-none-left')).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText('Close'));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});
