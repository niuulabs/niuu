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
  it('offers sign-in first and only when this install can run it', () => {
    const groups = providerGroups(MOCK_CATALOG, providers);
    expect(modesFor(groups.find((g) => g.key === 'anthropic')!)).toEqual(['signin', 'key']);
    expect(modesFor(groups.find((g) => g.key === 'deepseek')!)).toEqual(['key']);
    const github = providerGroups(
      MOCK_CATALOG.map((e) => (e.slug === 'github' ? { ...e, signInAvailable: false } : e)),
      git,
    ).find((g) => g.key === 'github')!;
    expect(modesFor(github)).toEqual(['key']);
    expect(github.signInEntry).toBeUndefined();
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

  it('still offers the other method for a provider that is already signed in', () => {
    const groups = providerGroups(MOCK_CATALOG, providers);
    const signedIn = {
      id: 'c1',
      slug: 'claude-code',
      integrationType: 'ai_provider',
      credentialName: 'claude-code-setup',
      enabled: true,
      config: {},
      credentialStatus: 'active',
    };
    renderWithSetup(<AddProviderDialog {...base} groups={groups} connections={[signedIn]} />);
    const row = screen.getByTestId('setup-add-pick-anthropic');
    expect(row).toHaveTextContent('Signed in');
    expect(row).toHaveTextContent('Use an API key');
    expect(row).not.toBeDisabled();
    fireEvent.click(row);
    // Only the key is left, so the method question is skipped.
    expect(screen.getByTestId('setup-add-intro-key').querySelector('a')).toHaveAttribute(
      'href',
      'https://console.anthropic.com/settings/keys',
    );
    expect(screen.getByTestId('setup-input-anthropic-api_key')).toBeInTheDocument();
  });

  it('disables a provider with nothing left to add', () => {
    const groups = providerGroups(MOCK_CATALOG, providers);
    const keyed = {
      id: 'c2',
      slug: 'deepseek',
      integrationType: 'ai_provider',
      credentialName: 'deepseek-setup',
      enabled: true,
      config: {},
      credentialStatus: 'valid',
    };
    renderWithSetup(<AddProviderDialog {...base} groups={groups} connections={[keyed]} />);
    expect(screen.getByTestId('setup-add-pick-deepseek')).toBeDisabled();
    expect(screen.getByTestId('setup-add-pick-deepseek')).toHaveTextContent('API key');
  });

  it('introduces the sign-in before the button', () => {
    const groups = providerGroups(MOCK_CATALOG, providers);
    renderWithSetup(
      <AddProviderDialog {...base} groups={groups} initialGroupKey="openai" initialMode="signin" />,
    );
    expect(screen.getByTestId('setup-add-intro-signin')).toHaveTextContent('short code');
    expect(screen.getByTestId('setup-signin-start-codex')).toBeInTheDocument();
  });

  it('goes straight to the token form when this install cannot run the sign-in', () => {
    const catalog = MOCK_CATALOG.map((e) =>
      e.slug === 'github' ? { ...e, signInAvailable: false } : e,
    );
    const groups = providerGroups(catalog, git);
    renderWithSetup(<AddProviderDialog {...base} noun="Git host" groups={groups} />);
    const row = screen.getByTestId('setup-add-pick-github');
    expect(row).not.toHaveTextContent('Sign in');
    expect(row).toHaveTextContent('Use a personal access token');
    fireEvent.click(row);
    expect(screen.queryByTestId('setup-add-mode-signin')).not.toBeInTheDocument();
    expect(screen.getByTestId('setup-input-github-token')).toBeInTheDocument();
    expect(screen.getByTestId('setup-add-dialog')).not.toHaveTextContent('oauth.clients');
  });

  it('says when nothing is left to add and resets on close', () => {
    const onOpenChange = vi.fn();
    renderWithSetup(<AddProviderDialog {...base} groups={[]} onOpenChange={onOpenChange} />);
    expect(screen.getByTestId('setup-add-none-left')).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText('Close'));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});
