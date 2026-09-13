import { describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { renderWithSetup } from '../testing/renderWithSetup';
import { createMockSetupService, MOCK_CATALOG } from '../adapters/mock';
import type { Enrollment } from '../domain/setup';
import type { ISetupService } from '../ports';
import { SignInCard } from './SignInCard';

const claudeCode = MOCK_CATALOG.find((entry) => entry.slug === 'claude-code')!;
const codex = MOCK_CATALOG.find((entry) => entry.slug === 'codex')!;

describe('SignInCard', () => {
  it('renders the connected state when a connection exists', () => {
    renderWithSetup(
      <SignInCard
        entry={claudeCode}
        connection={{
          id: 'c1',
          slug: 'claude-code',
          integrationType: 'ai_provider',
          credentialName: 'claude-code-setup',
          enabled: true,
          config: {},
          credentialStatus: 'valid',
        }}
      />,
    );
    expect(screen.getByText('Connected')).toBeInTheDocument();
    expect(screen.getByTestId('setup-signin-done-claude-code')).toHaveTextContent(
      'claude-code-setup',
    );
    expect(screen.queryByTestId('setup-signin-start-claude-code')).not.toBeInTheDocument();
    expect(screen.queryByTestId('setup-test-claude-code')).not.toBeInTheDocument();
  });

  it('tests a signed-in connection and shows the outcome', () => {
    const onTest = vi.fn();
    const connection = {
      id: 'c1',
      slug: 'github',
      integrationType: 'source_control',
      credentialName: 'github-signin',
      enabled: true,
      config: {},
      credentialStatus: 'active',
    };
    const github = MOCK_CATALOG.find((entry) => entry.slug === 'github')!;
    const first = renderWithSetup(
      <SignInCard entry={github} connection={connection} onTest={onTest} testing={false} />,
    );
    fireEvent.click(screen.getByTestId('setup-test-github'));
    expect(onTest).toHaveBeenCalledWith('c1');
    first.unmount();
    renderWithSetup(
      <SignInCard
        entry={github}
        connection={connection}
        onTest={onTest}
        testing={false}
        testResult={{
          success: true,
          provider: 'GitHubProvider',
          workspace: null,
          user: 'octocat',
          error: null,
          detail: '7 repositories reachable',
          repositories: ['a/b', 'c/d', 'e/f', 'g/h', 'i/j', 'k/l', 'm/n'],
        }}
      />,
    );
    expect(screen.getByTestId('setup-test-ok-github')).toHaveTextContent(
      '7 repositories reachable',
    );
    expect(screen.getByTestId('setup-test-repos-github')).toHaveTextContent('and 2 more');
  });

  it('offers to sign in again when the connection still needs auth', async () => {
    const service = createMockSetupService({ latencyMs: 0 });
    const startSpy = vi.spyOn(service, 'startEnrollment');
    renderWithSetup(
      <SignInCard
        entry={claudeCode}
        connection={{
          id: 'c1',
          slug: 'claude-code',
          integrationType: 'ai_provider',
          credentialName: 'claude-code-probe',
          enabled: true,
          config: {},
          credentialStatus: 'auth_required',
        }}
      />,
      { service },
    );
    expect(screen.getByText('Not connected')).toBeInTheDocument();
    expect(screen.getByTestId('setup-signin-needed-claude-code')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('setup-signin-start-claude-code'));
    await waitFor(() =>
      expect(startSpy).toHaveBeenCalledWith('claude-code', 'claude-code-probe', undefined),
    );
  });

  it('runs a device-code sign-in to completion by polling', async () => {
    const service = createMockSetupService({ latencyMs: 0, enrollmentPolls: 1 });
    renderWithSetup(<SignInCard entry={codex} connection={undefined} />, { service });
    fireEvent.click(screen.getByTestId('setup-signin-start-codex'));
    await waitFor(() =>
      expect(screen.getByTestId('setup-signin-link-codex')).toHaveAttribute(
        'href',
        'https://auth.openai.com/codex/device',
      ),
    );
    expect(screen.getByTestId('setup-signin-code-codex')).toHaveTextContent('MOCK-1234');
    expect(screen.getByText(/Waiting for the provider/)).toBeInTheDocument();
    // Polling continues in the background; the mock completes after one more read.
    await waitFor(() => expect(screen.getByTestId('setup-signin-done-codex')).toBeInTheDocument(), {
      timeout: 6000,
    });
    expect(await service.listIntegrations()).toHaveLength(1);
  }, 10000);

  it('takes the authorization code for a browser sign-in and cancels', async () => {
    const service = createMockSetupService({ latencyMs: 0 });
    renderWithSetup(<SignInCard entry={claudeCode} connection={undefined} />, { service });
    fireEvent.click(screen.getByTestId('setup-signin-start-claude-code'));
    await waitFor(() =>
      expect(screen.getByTestId('setup-signin-link-claude-code')).toBeInTheDocument(),
    );
    expect(screen.queryByTestId('setup-signin-code-claude-code')).not.toBeInTheDocument();
    expect(screen.getByTestId('setup-signin-submit-claude-code')).toBeDisabled();
    fireEvent.change(screen.getByTestId('setup-signin-input-claude-code'), {
      target: { value: '  abc-123  ' },
    });
    fireEvent.click(screen.getByTestId('setup-signin-submit-claude-code'));
    await waitFor(() =>
      expect(screen.getByTestId('setup-signin-done-claude-code')).toBeInTheDocument(),
    );
    expect(await service.listIntegrations()).toHaveLength(1);
  });

  it('cancels a running sign-in and explains the outcome', async () => {
    const service = createMockSetupService({ latencyMs: 0 });
    renderWithSetup(<SignInCard entry={claudeCode} connection={undefined} />, { service });
    fireEvent.click(screen.getByTestId('setup-signin-start-claude-code'));
    await waitFor(() =>
      expect(screen.getByTestId('setup-signin-cancel-claude-code')).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByTestId('setup-signin-cancel-claude-code'));
    await waitFor(() =>
      expect(screen.getByTestId('setup-signin-failed-claude-code')).toHaveTextContent(
        'Sign-in cancelled.',
      ),
    );
    expect(screen.getByTestId('setup-signin-start-claude-code')).toBeInTheDocument();
  });

  it('shows the starting state and surfaces errors', async () => {
    const enrollment: Enrollment = {
      id: 'e1',
      connectionId: 'c',
      providerSlug: 'codex',
      credentialName: 'codex-setup',
      state: 'pending',
      verificationUri: '',
      userCode: '',
      expiresAt: 'later',
      errorCode: '',
      inputRequired: false,
    };
    const service = {
      ...createMockSetupService({ latencyMs: 0 }),
      startEnrollment: vi.fn(async () => enrollment),
      getEnrollment: vi.fn(async () => {
        throw new Error('helper unreachable');
      }),
    } as unknown as ISetupService;
    renderWithSetup(<SignInCard entry={codex} connection={undefined} />, { service });
    fireEvent.click(screen.getByTestId('setup-signin-start-codex'));
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('helper unreachable'));
  });

  it('explains a failed helper start', async () => {
    const service = {
      ...createMockSetupService({ latencyMs: 0 }),
      startEnrollment: vi.fn(async () => {
        throw new Error('Could not start provider login');
      }),
    } as unknown as ISetupService;
    renderWithSetup(<SignInCard entry={codex} connection={undefined} />, { service });
    fireEvent.click(screen.getByTestId('setup-signin-start-codex'));
    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent('Could not start provider login'),
    );
    expect(screen.getByTestId('setup-signin-start-codex')).not.toBeDisabled();
  });
});
