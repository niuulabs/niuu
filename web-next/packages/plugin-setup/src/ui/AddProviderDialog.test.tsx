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

  it('adds a second account under its own name', () => {
    const onConnect = vi.fn();
    const onSelection = vi.fn();
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
    const keyed = { ...signedIn, id: 'c2', slug: 'anthropic', credentialName: 'anthropic-setup' };
    renderWithSetup(
      <AddProviderDialog
        {...base}
        groups={groups}
        connections={[signedIn, keyed]}
        onConnect={onConnect}
        onSelection={onSelection}
      />,
    );
    const row = screen.getByTestId('setup-add-pick-anthropic');
    expect(row).toHaveTextContent('2 accounts');
    expect(row).toHaveTextContent('Sign in');
    expect(row).toHaveTextContent('Use an API key');
    fireEvent.click(row);
    fireEvent.click(screen.getByTestId('setup-add-mode-key'));
    // The default name is taken by the existing key, so the form waits for a name.
    expect(screen.getByTestId('setup-add-dialog')).toHaveTextContent('Already connected: default');
    expect(screen.getByTestId('setup-add-dialog')).toHaveTextContent('already in use');
    expect(screen.getByTestId('setup-connect-anthropic')).toBeDisabled();
    fireEvent.change(screen.getByTestId('setup-add-account-name'), { target: { value: 'Work' } });
    expect(screen.getByTestId('setup-add-dialog')).not.toHaveTextContent('already in use');
    expect(onSelection).toHaveBeenLastCalledWith({
      groupKey: 'anthropic',
      mode: 'key',
      credentialName: 'anthropic-work',
    });
    fireEvent.change(screen.getByTestId('setup-input-anthropic-api_key'), {
      target: { value: 'sk-ant-2' },
    });
    fireEvent.click(screen.getByTestId('setup-connect-anthropic'));
    expect(onConnect).toHaveBeenCalledWith(
      expect.objectContaining({ slug: 'anthropic', credentialName: 'anthropic-work' }),
    );
  });

  it('finishes a pending sign-in on the account that started it', () => {
    const groups = providerGroups(MOCK_CATALOG, providers);
    const pending = {
      id: 'c1',
      slug: 'codex',
      integrationType: 'ai_provider',
      credentialName: 'codex-work',
      enabled: true,
      config: {},
      credentialStatus: 'auth_required',
    };
    renderWithSetup(
      <AddProviderDialog
        {...base}
        groups={groups}
        connections={[pending]}
        initialGroupKey="openai"
        initialMode="signin"
        initialCredentialName="codex-work"
      />,
    );
    expect(screen.queryByTestId('setup-add-account-name')).not.toBeInTheDocument();
    expect(screen.getByTestId('setup-signin-needed-codex')).toBeInTheDocument();
    expect(screen.getByTestId('setup-signin-start-codex')).not.toBeDisabled();
  });

  it('introduces the sign-in before the button', () => {
    const groups = providerGroups(MOCK_CATALOG, providers);
    renderWithSetup(
      <AddProviderDialog {...base} groups={groups} initialGroupKey="openai" initialMode="signin" />,
    );
    expect(screen.getByTestId('setup-add-intro-signin')).toHaveTextContent('short code');
    expect(screen.getByTestId('setup-signin-start-codex')).toBeInTheDocument();
  });

  it('asks for the person’s own application before a GitHub sign-in', async () => {
    const catalog = MOCK_CATALOG.map((e) =>
      e.slug === 'github' ? { ...e, signInAvailable: false, signInNeedsApp: true } : e,
    );
    const service = createMockSetupService({ latencyMs: 0, catalog });
    const groups = providerGroups(catalog, git);
    renderWithSetup(
      <AddProviderDialog
        {...base}
        noun="Git host"
        groups={groups}
        initialGroupKey="github"
        initialMode="signin"
      />,
      { service },
    );
    const form = screen.getByTestId('setup-oauth-app-github');
    expect(form).toHaveTextContent('Enable Device Flow');
    expect(form.querySelector('a')).toHaveAttribute(
      'href',
      'https://github.com/settings/applications/new',
    );
    expect(form).not.toHaveTextContent('oauth.clients');
    fireEvent.click(screen.getByTestId('setup-oauth-app-save-github'));
    expect(form).toHaveTextContent('Client ID is required');
    fireEvent.change(screen.getByTestId('setup-oauth-app-id-github'), {
      target: { value: ' Iv1.mine ' },
    });
    fireEvent.click(screen.getByTestId('setup-oauth-app-save-github'));
    await waitFor(async () => {
      const [entry] = (await service.listCatalog()).filter((e) => e.slug === 'github');
      expect(entry?.signInAvailable).toBe(true);
    });
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

  it('resets on close', () => {
    const onOpenChange = vi.fn();
    const onSelection = vi.fn();
    const groups = providerGroups(MOCK_CATALOG, providers);
    renderWithSetup(
      <AddProviderDialog
        {...base}
        groups={groups}
        onOpenChange={onOpenChange}
        onSelection={onSelection}
        initialGroupKey="deepseek"
      />,
    );
    fireEvent.click(screen.getByLabelText('Close'));
    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(onSelection).toHaveBeenLastCalledWith({
      groupKey: null,
      mode: null,
      credentialName: null,
    });
  });
});
