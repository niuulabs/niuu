import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { MOCK_CATALOG, MOCK_SYSTEM } from '../adapters/mock';
import { WIZARD_STEPS } from '../domain/setup';
import { renderWithSetup } from '../testing/renderWithSetup';
import { FinishStep, summarizeConnections } from './FinishStep';
import { IntegrationCard } from './IntegrationCard';
import { IntegrationsStep } from './IntegrationsStep';
import { SetupRail } from './SetupRail';
import { SystemStep } from './SystemStep';
import { WelcomeStep } from './WelcomeStep';

const github = MOCK_CATALOG.find((entry) => entry.slug === 'github')!;
const claudeCode = MOCK_CATALOG.find((entry) => entry.slug === 'claude-code')!;
const connection = {
  id: 'c1',
  slug: 'github',
  integrationType: 'source_control',
  credentialName: 'github-setup',
  enabled: true,
  config: {},
  credentialStatus: 'valid',
};

describe('WelcomeStep', () => {
  it('shows host chips and begins', () => {
    const onBegin = vi.fn();
    render(<WelcomeStep facts={MOCK_SYSTEM.host} loading={false} onBegin={onBegin} />);
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('this DGX Spark');
    expect(screen.getByTestId('setup-host-chips')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('setup-begin'));
    expect(onBegin).toHaveBeenCalledOnce();
  });

  it('shows loading and no chips without facts', () => {
    const { rerender } = render(<WelcomeStep facts={null} loading onBegin={vi.fn()} />);
    expect(screen.getByTestId('setup-welcome-loading')).toBeInTheDocument();
    rerender(<WelcomeStep facts={null} loading={false} onBegin={vi.fn()} />);
    expect(screen.queryByTestId('setup-host-chips')).not.toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('this machine');
  });
});

describe('SystemStep', () => {
  it('renders host facts and check rows', () => {
    render(<SystemStep report={MOCK_SYSTEM} loading={false} error={null} onRerun={vi.fn()} />);
    expect(screen.getByText('spark')).toBeInTheDocument();
    expect(screen.getByTestId('setup-check-database')).toBeInTheDocument();
    expect(
      screen.getByTestId('setup-check-host-facts').querySelector('.setup-row__icon--ok'),
    ).not.toBeNull();
    expect(screen.getByText(/12 checks · 0 failed · 0 warnings/)).toBeInTheDocument();
    expect(screen.getByTestId('setup-host-checks')).toBeInTheDocument();
    expect(screen.getByTestId('setup-check-outbound-network')).toBeInTheDocument();
    expect(screen.queryByTestId('setup-system-blocked')).not.toBeInTheDocument();
  });

  it('shows a passed warn-only check as a warning', () => {
    const report = {
      host: { ...MOCK_SYSTEM.host!, checks: [] },
      checks: [{ name: 'docker socket', passed: true, warnOnly: true, message: 'sudo needed' }],
      healthy: true,
    };
    render(<SystemStep report={report} loading={false} error={null} onRerun={vi.fn()} />);
    expect(screen.getByText(/1 checks · 0 failed · 1 warnings/)).toBeInTheDocument();
    expect(screen.queryByTestId('setup-host-checks')).not.toBeInTheDocument();
  });

  it('shows failures, warnings, errors and re-runs', () => {
    const onRerun = vi.fn();
    const report = {
      host: { ...MOCK_SYSTEM.host!, gpus: [], docker_version: '', checks: [] },
      checks: [
        { name: 'database', passed: false, warnOnly: false, message: 'down' },
        { name: 'docker socket', passed: false, warnOnly: true, message: 'missing' },
      ],
      healthy: false,
    };
    render(
      <SystemStep report={report} loading={false} error={new Error('boom')} onRerun={onRerun} />,
    );
    expect(screen.getByText('No NVIDIA GPU')).toBeInTheDocument();
    expect(screen.getByText(/2 checks · 1 failed · 1 warnings/)).toBeInTheDocument();
    expect(
      screen.getByTestId('setup-check-database').querySelector('.setup-row__icon--fail'),
    ).not.toBeNull();
    expect(
      screen.getByTestId('setup-check-docker-socket').querySelector('.setup-row__icon--warn'),
    ).not.toBeNull();
    expect(screen.getByRole('alert')).toHaveTextContent('boom');
    expect(screen.getByTestId('setup-system-blocked')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('setup-system-rerun'));
    expect(onRerun).toHaveBeenCalledOnce();
  });

  it('renders the loading title without a report', () => {
    render(<SystemStep report={undefined} loading error={null} onRerun={vi.fn()} />);
    expect(screen.getByText('Checking…')).toBeInTheDocument();
    expect(screen.getByTestId('setup-system-rerun')).toBeDisabled();
  });
});

describe('IntegrationCard', () => {
  const base = {
    connection: undefined,
    connecting: false,
    connectError: null,
    testResult: undefined,
    testing: false,
    onConnect: vi.fn(),
    onTest: vi.fn(),
  };

  it('validates required credential fields before connecting', () => {
    const onConnect = vi.fn();
    render(<IntegrationCard {...base} entry={github} onConnect={onConnect} />);
    fireEvent.click(screen.getByTestId('setup-connect-github'));
    expect(onConnect).not.toHaveBeenCalled();
    expect(screen.getByRole('alert')).toHaveTextContent('Personal Access Token is required');

    fireEvent.change(screen.getByTestId('setup-input-github-token'), {
      target: { value: 'ghp_x' },
    });
    fireEvent.change(screen.getByTestId('setup-config-github-orgs'), {
      target: { value: 'niuulabs' },
    });
    fireEvent.click(screen.getByTestId('setup-connect-github'));
    expect(onConnect).toHaveBeenCalledWith({
      slug: 'github',
      credentialName: 'github-setup',
      credential: { token: 'ghp_x' },
      config: { base_url: 'https://api.github.com', orgs: ['niuulabs'] },
    });
  });

  it('shows connecting state and errors', () => {
    render(
      <IntegrationCard {...base} entry={github} connecting connectError={new Error('denied')} />,
    );
    expect(screen.getByTestId('setup-connect-github')).toBeDisabled();
    expect(screen.getByText('Connecting…')).toBeInTheDocument();
    expect(screen.getByText('denied')).toBeInTheDocument();
  });

  it('renders the connected state with test results', () => {
    const onTest = vi.fn();
    const { rerender } = render(
      <IntegrationCard {...base} entry={github} connection={connection} onTest={onTest} />,
    );
    expect(screen.getByText('Connected')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('setup-test-github'));
    expect(onTest).toHaveBeenCalledWith('c1');

    rerender(
      <IntegrationCard
        {...base}
        entry={github}
        connection={connection}
        testResult={{
          success: true,
          provider: 'GitHub',
          workspace: 'niuulabs',
          user: 'me',
          error: null,
          detail: '3 repositories reachable',
          repositories: ['niuulabs/volundr', 'niuulabs/skuld', 'niuulabs/site'],
        }}
      />,
    );
    expect(screen.getByTestId('setup-test-ok-github')).toHaveTextContent(
      '3 repositories reachable',
    );
    expect(screen.getByTestId('setup-test-repos-github')).toHaveTextContent('niuulabs/volundr');
    rerender(
      <IntegrationCard
        {...base}
        entry={github}
        connection={connection}
        testing
        testResult={{
          success: false,
          provider: 'GitHub',
          workspace: null,
          user: null,
          error: null,
          detail: null,
          repositories: [],
        }}
      />,
    );
    expect(screen.getByTestId('setup-test-failed-github')).toHaveTextContent('Test failed');
    expect(screen.getByTestId('setup-test-github')).toBeDisabled();
  });

  it('points interactive-only providers at the sign-in card', () => {
    render(<IntegrationCard {...base} entry={claudeCode} />);
    expect(screen.getByText('Needs interactive sign-in')).toBeInTheDocument();
    expect(screen.queryByTestId('setup-form-claude-code')).not.toBeInTheDocument();
  });
});

describe('IntegrationsStep', () => {
  const step = WIZARD_STEPS.find((candidate) => candidate.id === 'git')!;
  const props = {
    step,
    loading: false,
    error: null,
    connectingSlug: null,
    connectErrorSlug: null,
    connectError: null,
    testingId: null,
    testResults: {},
    onConnect: vi.fn(),
    onTest: vi.fn(),
  };

  it('lists connected providers as rows with a test action', () => {
    const onTest = vi.fn();
    renderWithSetup(
      <IntegrationsStep
        {...props}
        onTest={onTest}
        catalog={MOCK_CATALOG}
        connections={[connection]}
        testResults={{
          c1: {
            success: true,
            provider: 'GitHubProvider',
            workspace: null,
            user: 'octocat',
            error: null,
            detail: '2 repositories reachable',
            repositories: ['niuulabs/volundr', 'niuulabs/skuld'],
          },
        }}
      />,
    );
    expect(screen.getByTestId('setup-provider-row-c1')).toHaveTextContent('GitHub');
    expect(screen.getByTestId('setup-provider-row-c1')).toHaveTextContent('Personal access token');
    expect(screen.getByTestId('setup-provider-row-c1')).not.toHaveTextContent('· default');
    expect(screen.queryByTestId('setup-provider-empty')).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId('setup-test-c1'));
    expect(onTest).toHaveBeenCalledWith('c1');
    expect(screen.getByTestId('setup-test-ok-github')).toHaveTextContent('2 repositories');
  });

  it('lists every account of a provider as its own row', () => {
    const work = { ...connection, id: 'c2', credentialName: 'github-work' };
    renderWithSetup(
      <IntegrationsStep {...props} catalog={MOCK_CATALOG} connections={[connection, work]} />,
    );
    expect(screen.getByTestId('setup-provider-row-c1')).not.toHaveTextContent('· default');
    expect(screen.getByTestId('setup-provider-row-c2')).toHaveTextContent('GitHub · work');
    expect(screen.getByTestId('setup-test-c2')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('setup-provider-add'));
    expect(screen.getByTestId('setup-add-pick-github')).toHaveTextContent('2 accounts');
    expect(screen.getByTestId('setup-add-pick-github')).not.toBeDisabled();
  });

  it('shows the empty state and opens the add dialog', () => {
    renderWithSetup(<IntegrationsStep {...props} catalog={MOCK_CATALOG} connections={[]} />);
    expect(screen.getByTestId('setup-provider-empty')).toHaveTextContent('No Git hosts yet');
    fireEvent.click(screen.getByTestId('setup-provider-add'));
    expect(screen.getByTestId('setup-add-dialog')).toBeInTheDocument();
    expect(screen.getByTestId('setup-add-pick-github')).toBeInTheDocument();
    expect(screen.queryByTestId('setup-add-pick-linear')).not.toBeInTheDocument();
  });

  it('shows token expiry and the reason a sign-in is needed', () => {
    renderWithSetup(
      <IntegrationsStep
        {...props}
        catalog={MOCK_CATALOG}
        connections={[
          {
            ...connection,
            credentialStatus: 'auth_required',
            credentialExpiresAt: new Date(Date.now() - 60_000).toISOString(),
            credentialErrorCode: 'refresh_failed',
          },
        ]}
      />,
    );
    expect(screen.getByTestId('setup-provider-expiry-c1')).toHaveTextContent('Token expired');
    expect(screen.getByTestId('setup-provider-problem-c1')).toHaveTextContent(
      'could not be renewed automatically',
    );
  });

  it('keeps the dialog open when the default name already belongs to an account', () => {
    renderWithSetup(
      <IntegrationsStep {...props} catalog={MOCK_CATALOG} connections={[connection]} />,
    );
    fireEvent.click(screen.getByTestId('setup-provider-add'));
    fireEvent.click(screen.getByTestId('setup-add-pick-github'));
    fireEvent.click(screen.getByTestId('setup-add-mode-key'));
    // github-setup is taken by the existing account: the dialog must stay and say so.
    expect(screen.getByTestId('setup-add-dialog')).toBeInTheDocument();
    expect(screen.getByTestId('setup-add-dialog')).toHaveTextContent('already in use');
    expect(screen.getByTestId('setup-connect-github')).toBeDisabled();
    fireEvent.change(screen.getByTestId('setup-add-account-name'), { target: { value: 'work' } });
    expect(screen.getByTestId('setup-add-dialog')).not.toHaveTextContent('already in use');
    expect(screen.getByTestId('setup-connect-github')).not.toBeDisabled();
  });

  it('checks a host right after it is connected and closes the dialog', () => {
    const onTest = vi.fn();
    function Harness() {
      const [connections, setConnections] = useState<(typeof connection)[]>([]);
      return (
        <>
          <button
            type="button"
            data-testid="simulate-connected"
            onClick={() => setConnections([connection])}
          />
          <IntegrationsStep
            {...props}
            onTest={onTest}
            catalog={MOCK_CATALOG}
            connections={connections}
          />
        </>
      );
    }
    renderWithSetup(<Harness />);
    fireEvent.click(screen.getByTestId('setup-provider-add'));
    fireEvent.click(screen.getByTestId('setup-add-pick-github'));
    fireEvent.click(screen.getByTestId('setup-add-mode-key'));
    expect(screen.getByTestId('setup-input-github-token')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('simulate-connected'));
    expect(onTest).toHaveBeenCalledTimes(1);
    expect(onTest).toHaveBeenCalledWith('c1');
    expect(screen.queryByTestId('setup-add-dialog')).not.toBeInTheDocument();
    expect(screen.getByTestId('setup-provider-row-c1')).toBeInTheDocument();
  });

  it('offers to finish a pending sign-in', () => {
    renderWithSetup(
      <IntegrationsStep
        {...props}
        catalog={MOCK_CATALOG}
        connections={[{ ...connection, credentialStatus: 'auth_required' }]}
      />,
    );
    expect(screen.getByTestId('setup-provider-row-c1')).toHaveTextContent('Sign-in needed');
    fireEvent.click(screen.getByTestId('setup-provider-finish-c1'));
    expect(screen.getByTestId('setup-add-dialog')).toBeInTheDocument();
    expect(screen.getByTestId('setup-signin-start-github')).toBeInTheDocument();
  });

  it('shows loading, error and empty catalog states', () => {
    const { rerender } = renderWithSetup(
      <IntegrationsStep {...props} catalog={undefined} connections={undefined} loading />,
    );
    expect(screen.getByText('Loading catalog…')).toBeInTheDocument();
    expect(screen.getByTestId('setup-provider-add')).toBeDisabled();
    rerender(
      <IntegrationsStep
        {...props}
        catalog={undefined}
        connections={undefined}
        error={new Error('boom')}
      />,
    );
    expect(screen.getByRole('alert')).toHaveTextContent('boom');
  });
});

describe('FinishStep', () => {
  it('summarises connections by type', () => {
    const rows = summarizeConnections([
      connection,
      { ...connection, id: 'c2', slug: 'linear', integrationType: 'issue_tracker' },
      { ...connection, id: 'c3', slug: 'off', enabled: false },
      { ...connection, id: 'c4', slug: '', integrationType: 'other', credentialName: 'cred' },
    ]);
    expect(rows).toEqual([
      { label: 'AI providers', value: 'none' },
      { label: 'Git', value: 'github' },
      { label: 'Tickets', value: 'linear' },
    ]);
    expect(summarizeConnections(undefined).every((row) => row.value === 'none')).toBe(true);
  });

  it('renders the summary, saving and error states', () => {
    const state = {
      enabled: true,
      mode: 'docker',
      completed: false,
      completedAt: null,
      steps: [],
      completedSteps: [],
    };
    const { rerender } = render(
      <FinishStep
        state={state}
        connections={[connection]}
        stack={undefined}
        apply={undefined}
        applying={false}
        reconnecting={false}
        newAddress={null}
        finishing
        error={null}
      />,
    );
    expect(screen.getByText('Saving…')).toBeInTheDocument();
    expect(screen.getByText(/docker mode/)).toBeInTheDocument();
    rerender(
      <FinishStep
        state={undefined}
        connections={[]}
        stack={undefined}
        apply={undefined}
        applying={false}
        reconnecting={false}
        newAddress={null}
        finishing={false}
        error={new Error('no')}
      />,
    );
    expect(screen.getByRole('alert')).toHaveTextContent('no');
    expect(screen.getByText(/running/)).toBeInTheDocument();
  });
});

describe('SetupRail', () => {
  it('marks done and current steps and only allows going back', () => {
    const onSelect = vi.fn();
    const state = {
      enabled: true,
      mode: 'docker',
      completed: false,
      completedAt: null,
      steps: [],
      completedSteps: [{ step: 'system', completedAt: 'now', data: {} }],
    };
    render(<SetupRail current="providers" state={state} hostname="spark" onSelect={onSelect} />);
    expect(screen.getByTestId('setup-rail-providers')).toHaveAttribute('aria-current', 'step');
    expect(screen.getByTestId('setup-rail-git')).toBeDisabled();
    fireEvent.click(screen.getByTestId('setup-rail-system'));
    expect(onSelect).toHaveBeenCalledWith('system');
    expect(screen.getByText('spark')).toBeInTheDocument();
    expect(screen.getByText('docker mode')).toBeInTheDocument();
  });

  it('falls back to the brand name without a hostname', () => {
    render(<SetupRail current="welcome" state={undefined} hostname={null} onSelect={vi.fn()} />);
    expect(screen.getByText('niuu')).toBeInTheDocument();
  });
});
