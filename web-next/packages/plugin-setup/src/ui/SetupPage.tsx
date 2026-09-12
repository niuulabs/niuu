import { useEffect, useRef, useState } from 'react';
import {
  WIZARD_STEPS,
  backendStepId,
  hostFlavor,
  isStepDone,
  nextStep,
  originLostAfterApply,
  previousStep,
  type IntegrationTestResult,
  type SetupState,
  type WizardStepId,
} from '../domain/setup';
import { FinishStep } from './FinishStep';
import { IntegrationsStep } from './IntegrationsStep';
import { ModelStep } from './ModelStep';
import { RuntimeStep } from './RuntimeStep';
import { SetupRail } from './SetupRail';
import { SystemStep } from './SystemStep';
import { WelcomeStep } from './WelcomeStep';
import { ArrowIcon, BackIcon } from './icons';
import {
  isApplySettled,
  useApplyStack,
  useCatalog,
  useCompleteSetup,
  useCompleteStep,
  useConnectIntegration,
  useIntegrations,
  useSetupState,
  useStack,
  useStackStatus,
  useStageStack,
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
  model: {
    title: 'Run a model on this host?',
    lede: 'vLLM serves it here and Bifrost routes to it. Sessions, workflows and residents can then work without code leaving the box.',
  },
  providers: {
    title: 'Connect AI providers',
    lede: 'Sign in to the ones you use. Niuu stores tokens encrypted on this machine and only ever sends them to that provider.',
  },
  git: {
    title: 'Connect Git',
    lede: 'Sessions clone from and push to these. Local folders on this host are mounted straight into session sandboxes.',
  },
  tracker: {
    title: 'Where does work come from?',
    lede: 'Ting turns issues into sagas and dispatches them. Optional for now.',
  },
  runtime: {
    title: 'Runtime and access',
    lede: 'Where sessions execute, where their files live, and who can reach this Niuu.',
  },
  finish: {
    title: 'Ready to go',
    lede: 'Review what you chose, then open Niuu.',
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
  /** This page's origin, for the "address stops working" warning. */
  origin?: string;
}

export function SetupPage({ onNavigate, origin }: SetupPageProps = {}) {
  // Full page load so the shell re-reads setup state; the query string is kept
  // so dev-only switches (e.g. ?config=default) survive.
  const navigate =
    onNavigate ?? ((path: string) => window.location.assign(`${path}${window.location.search}`));
  const pageOrigin =
    origin ?? (typeof window !== 'undefined' ? window.location.origin : 'http://127.0.0.1');
  const stateQuery = useSetupState();
  const systemQuery = useSystemReport();
  const catalogQuery = useCatalog();
  const integrationsQuery = useIntegrations();
  const stackQuery = useStack();
  const completeStep = useCompleteStep();
  const completeSetup = useCompleteSetup();
  const connect = useConnectIntegration();
  const test = useTestIntegration();
  const stage = useStageStack();
  const applyStack = useApplyStack();

  // `current` is only set once the user navigates; until then the screen is
  // derived from the persisted progress so a reload resumes where they were.
  const [current, setCurrent] = useState<WizardStepId | null>(null);
  const [testResults, setTestResults] = useState<Record<string, IntegrationTestResult>>({});
  // Set once "Open Niuu" started an apply; the status poll runs until it settles.
  const [applyStarted, setApplyStarted] = useState(false);
  const finishedRef = useRef(false);
  const statusQuery = useStackStatus(applyStarted);

  const step: WizardStepId =
    current ?? (stateQuery.data ? initialStep(stateQuery.data) : 'welcome');
  const definition = WIZARD_STEPS.find((candidate) => candidate.id === step) ?? WIZARD_STEPS[0]!;
  const index = WIZARD_STEPS.findIndex((candidate) => candidate.id === step);
  const host = systemQuery.data?.host ?? null;
  const stack = stackQuery.data;
  const stackUnavailable = stackQuery.error ?? null;
  const applyStatus = statusQuery.data;
  const watchingApply = applyStarted && !isApplySettled(applyStatus?.state);
  const reconnecting = watchingApply && statusQuery.isError;
  const newAddress =
    stack && originLostAfterApply(stack, pageOrigin)
      ? (stack.effective.accessUrls[stack.effective.accessUrls.length - 1] ?? null)
      : null;

  const complete = () => {
    completeStep.mutate({ step: backendStepId('finish') });
    completeSetup.mutate(undefined, { onSuccess: () => navigate('/ready') });
  };

  // Once the apply reports applied (the platform is back), finish setup once.
  useEffect(() => {
    if (!applyStarted || finishedRef.current) return;
    if (applyStatus?.state !== 'applied') return;
    finishedRef.current = true;
    complete();
    // `complete` is stable for the life of the page; the mutations are hooks.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [applyStatus?.state, applyStarted]);

  const advance = () => {
    const next = nextStep(step);
    const data =
      step === 'runtime' && stack
        ? { bind_host: stack.effective.bindHost, external_host: stack.effective.externalHost }
        : step === 'model' && stack
          ? { vllm_enabled: stack.effective.vllm.enabled, vllm_model: stack.effective.vllm.model }
          : undefined;
    completeStep.mutate({ step: backendStepId(step), data });
    if (next) setCurrent(next);
  };

  const finish = () => {
    if (stack?.hasStagedChanges) {
      applyStack.mutate(undefined, { onSuccess: () => setApplyStarted(true) });
      return;
    }
    complete();
  };

  const systemBlocked =
    step === 'system' &&
    (systemQuery.isLoading ||
      (systemQuery.data
        ? [...(systemQuery.data.host?.checks ?? []), ...systemQuery.data.checks].some(
            (c) => !c.passed && !('warnOnly' in c ? c.warnOnly : c.warn_only),
          )
        : false));

  if (step === 'welcome') {
    return (
      <div className="setup-page setup-page--hero" data-testid="setup-page">
        <WelcomeStep facts={host} loading={systemQuery.isLoading} onBegin={advance} />
      </div>
    );
  }

  const copy = STEP_COPY[step];
  const title = step === 'model' ? `Run a model on ${hostFlavor(host)}?` : copy.title;
  const finishBusy = applyStack.isPending || watchingApply || completeSetup.isPending;

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
              <h1 className="setup-title">
                {step === 'finish' && stack?.hasStagedChanges ? 'Bringing Niuu up' : title}
              </h1>
              <p className="setup-lede">{copy.lede}</p>
            </div>
            {step === 'system' ? (
              <SystemStep
                report={systemQuery.data}
                loading={systemQuery.isFetching}
                error={systemQuery.error}
                onRerun={() => systemQuery.refetch()}
              />
            ) : null}
            {step === 'model' ? (
              <ModelStep
                stack={stack}
                loading={stackQuery.isLoading}
                unavailable={stackUnavailable}
                staging={stage.isPending}
                stageError={stage.error}
                onStage={(changes) => stage.mutate(changes)}
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
            {step === 'runtime' ? (
              <RuntimeStep
                stack={stack}
                loading={stackQuery.isLoading}
                unavailable={stackUnavailable}
                staging={stage.isPending}
                stageError={stage.error}
                onStage={(changes) => stage.mutate(changes)}
              />
            ) : null}
            {step === 'finish' ? (
              <FinishStep
                state={stateQuery.data}
                connections={integrationsQuery.data}
                stack={stack}
                apply={applyStarted ? applyStatus : undefined}
                applying={watchingApply || applyStack.isPending}
                reconnecting={reconnecting}
                newAddress={newAddress}
                finishing={completeSetup.isPending}
                error={completeSetup.error ?? applyStack.error}
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
            disabled={finishBusy}
            data-testid="setup-back"
          >
            <BackIcon /> Back
          </button>
          {step === 'finish' ? (
            <button
              type="button"
              className="setup-btn setup-btn--primary"
              onClick={finish}
              disabled={finishBusy}
              data-testid="setup-finish-button"
            >
              {stack?.hasStagedChanges ? 'Apply and open Niuu' : 'Open Niuu'} <ArrowIcon />
            </button>
          ) : (
            <button
              type="button"
              className="setup-btn setup-btn--primary"
              onClick={advance}
              disabled={systemBlocked || stage.isPending}
              data-testid="setup-continue"
            >
              Continue <ArrowIcon />
            </button>
          )}
        </div>
      </main>
    </div>
  );
}
