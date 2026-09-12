import { useState } from 'react';
import {
  WIZARD_STEPS,
  backendStepId,
  isStepDone,
  nextStep,
  previousStep,
  type IntegrationTestResult,
  type SetupState,
  type WizardStepId,
} from '../domain/setup';
import { FinishStep } from './FinishStep';
import { IntegrationsStep } from './IntegrationsStep';
import { RuntimeStep } from './RuntimeStep';
import { SetupRail } from './SetupRail';
import { SystemStep } from './SystemStep';
import { WelcomeStep } from './WelcomeStep';
import { ArrowIcon, BackIcon } from './icons';
import {
  useCatalog,
  useCompleteSetup,
  useCompleteStep,
  useConnectIntegration,
  useIntegrations,
  useSetupState,
  useSystemReport,
  useTestIntegration,
} from './hooks';
import './SetupPage.css';

const STEP_COPY: Record<WizardStepId, { title: string; lede: string }> = {
  welcome: { title: '', lede: '' },
  system: {
    title: 'System check',
    lede: 'What this host looks like, and whether the platform can reach what it needs.',
  },
  providers: {
    title: 'Connect AI providers',
    lede: 'Keys are stored encrypted on this machine and only ever sent to that provider.',
  },
  git: {
    title: 'Connect Git',
    lede: 'Sessions clone from and push to these. Skip if you only work with local folders.',
  },
  tracker: {
    title: 'Where does work come from?',
    lede: 'Ting turns issues into sagas and dispatches them. Optional for now.',
  },
  runtime: {
    title: 'Runtime & access',
    lede: 'How sessions are isolated on this host, and who can reach the web app.',
  },
  finish: {
    title: 'Ready to go',
    lede: 'Review what you connected, then open Niuu.',
  },
};

/** First unfinished step, so a reload resumes where the user left off. */
export function initialStep(state: SetupState | undefined): WizardStepId {
  for (const step of WIZARD_STEPS) {
    if (step.id === 'finish') return 'finish';
    if (!isStepDone(state, step.id)) return step.id;
  }
  return 'finish';
}

export interface SetupPageProps {
  /** Navigation after finishing; defaults to a full page load of /ready. */
  onNavigate?: (path: string) => void;
}

export function SetupPage({ onNavigate }: SetupPageProps = {}) {
  // Full page load so the shell re-reads setup state; the query string is kept
  // so dev-only switches (e.g. ?config=default) survive.
  const navigate =
    onNavigate ?? ((path: string) => window.location.assign(`${path}${window.location.search}`));
  const stateQuery = useSetupState();
  const systemQuery = useSystemReport();
  const catalogQuery = useCatalog();
  const integrationsQuery = useIntegrations();
  const completeStep = useCompleteStep();
  const completeSetup = useCompleteSetup();
  const connect = useConnectIntegration();
  const test = useTestIntegration();

  // `current` is only set once the user navigates; until then the screen is
  // derived from the persisted progress so a reload resumes where they were.
  const [current, setCurrent] = useState<WizardStepId | null>(null);
  const [testResults, setTestResults] = useState<Record<string, IntegrationTestResult>>({});

  const step: WizardStepId =
    current ?? (stateQuery.data ? initialStep(stateQuery.data) : 'welcome');
  const definition = WIZARD_STEPS.find((candidate) => candidate.id === step) ?? WIZARD_STEPS[0]!;
  const index = WIZARD_STEPS.findIndex((candidate) => candidate.id === step);
  const host = systemQuery.data?.host ?? null;

  const advance = () => {
    const next = nextStep(step);
    const data =
      step === 'runtime' && host
        ? { bind_host: host.bind_host ?? '', external_host: host.external_host ?? '' }
        : undefined;
    completeStep.mutate({ step: backendStepId(step), data });
    if (next) setCurrent(next);
  };

  const finish = () => {
    completeStep.mutate({ step: backendStepId('finish') });
    completeSetup.mutate(undefined, { onSuccess: () => navigate('/ready') });
  };

  const systemBlocked =
    step === 'system' &&
    (systemQuery.isLoading ||
      (systemQuery.data ? systemQuery.data.checks.some((c) => !c.passed && !c.warnOnly) : false));

  if (step === 'welcome') {
    return (
      <div className="setup-page setup-page--hero" data-testid="setup-page">
        <WelcomeStep facts={host} loading={systemQuery.isLoading} onBegin={advance} />
      </div>
    );
  }

  return (
    <div className="setup-page" data-testid="setup-page">
      <SetupRail
        current={step}
        state={stateQuery.data}
        hostname={host?.hostname ?? null}
        onSelect={setCurrent}
      />
      <main className="setup-main">
        <div className="setup-content">
          <div className="setup-col">
            <div>
              <div className="setup-label">
                Step {index + 1} of {WIZARD_STEPS.length}
              </div>
              <h1 className="setup-title">{STEP_COPY[step].title}</h1>
              <p className="setup-lede">{STEP_COPY[step].lede}</p>
            </div>
            {step === 'system' ? (
              <SystemStep
                report={systemQuery.data}
                loading={systemQuery.isFetching}
                error={systemQuery.error}
                onRerun={() => systemQuery.refetch()}
              />
            ) : null}
            {definition.integrationType ? (
              <IntegrationsStep
                step={definition}
                catalog={catalogQuery.data}
                connections={integrationsQuery.data}
                loading={catalogQuery.isLoading || integrationsQuery.isLoading}
                error={catalogQuery.error ?? integrationsQuery.error}
                connectingSlug={connect.isPending ? (connect.variables?.slug ?? null) : null}
                connectErrorSlug={connect.error ? (connect.variables?.slug ?? null) : null}
                connectError={connect.error}
                testingId={test.isPending ? (test.variables ?? null) : null}
                testResults={testResults}
                onConnect={(input) => connect.mutate(input)}
                onTest={(id) =>
                  test.mutate(id, {
                    onSuccess: (result) => setTestResults((prev) => ({ ...prev, [id]: result })),
                  })
                }
              />
            ) : null}
            {step === 'runtime' ? <RuntimeStep facts={host} state={stateQuery.data} /> : null}
            {step === 'finish' ? (
              <FinishStep
                state={stateQuery.data}
                connections={integrationsQuery.data}
                finishing={completeSetup.isPending}
                error={completeSetup.error}
              />
            ) : null}
          </div>
        </div>
        <div className="setup-footer">
          <button
            type="button"
            className="setup-btn setup-btn--ghost"
            onClick={() => {
              const previous = previousStep(step);
              if (previous) setCurrent(previous);
            }}
            data-testid="setup-back"
          >
            <BackIcon /> Back
          </button>
          {step === 'finish' ? (
            <button
              type="button"
              className="setup-btn setup-btn--primary"
              onClick={finish}
              disabled={completeSetup.isPending}
              data-testid="setup-finish-button"
            >
              Open Niuu <ArrowIcon />
            </button>
          ) : (
            <button
              type="button"
              className="setup-btn setup-btn--primary"
              onClick={advance}
              disabled={systemBlocked}
              data-testid="setup-continue"
            >
              {definition.integrationType ? 'Continue' : 'Continue'} <ArrowIcon />
            </button>
          )}
        </div>
      </main>
    </div>
  );
}
