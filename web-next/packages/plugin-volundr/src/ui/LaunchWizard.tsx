import { Dialog, DialogContent } from '@niuulabs/ui';
import './LaunchWizard.css';
import { FALLBACK_SESSION_DEFINITIONS, launchSpecRef } from './launchWizardModel';
export * from './launchWizardModel';
import {
  STEPS,
  BootingStep,
  ConfirmStep,
  RuntimeStep,
  SourceStep,
  StepIndicator,
  type LaunchWizardProps,
} from './LaunchWizardSteps';
export * from './LaunchWizardSteps';
import { useLaunchWizard } from './useLaunchWizard';

export function LaunchWizard(props: LaunchWizardProps) {
  const { open, onOpenChange } = props;
  const {
    availableMcpServers,
    bootProgress,
    bootStep,
    canGoBack,
    canLaunch,
    clusterResources,
    createdSessionId,
    credentials,
    form,
    handleApplyPreset,
    handleBack,
    handleNext,
    handleSavePreset,
    integrations,
    isLastStep,
    launchError,
    launching,
    manualBranches,
    models,
    navigate,
    presets,
    repos,
    sessionDefinitions,
    step,
    targets,
    trackerLoading,
    trackerResults,
    update,
    workspaces,
  } = useLaunchWizard(props);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        title={step === 'booting' ? 'Forging\u2026' : 'Launch pod'}
        className="vol-launch-wizard"
        data-testid="launch-wizard"
      >
        <div className="niuu:flex niuu:flex-col niuu:gap-4 vol-launch-wizard__body">
          {/* Step indicator */}
          {step !== 'booting' && <StepIndicator current={step} steps={STEPS} />}

          {/* Step content */}
          {step === 'source' && (
            <SourceStep
              form={form}
              update={update}
              repos={repos}
              branchOptions={
                repos.find((repo) => repo.cloneUrl === form.repo)?.branches.length
                  ? (repos.find((repo) => repo.cloneUrl === form.repo)?.branches ?? [])
                  : manualBranches
              }
              trackerResults={trackerResults}
              trackerLoading={trackerLoading}
            />
          )}
          {step === 'runtime' && (
            <RuntimeStep
              form={form}
              update={update}
              models={models}
              workspaces={workspaces}
              targets={targets}
              credentials={credentials}
              integrations={integrations}
              clusterResources={clusterResources}
              presets={presets}
              selectedPreset={
                presets.find((preset) => launchSpecRef(preset) === form.presetId) ?? null
              }
              availableMcpServers={availableMcpServers}
              sessionDefinitions={
                sessionDefinitions.length > 0 ? sessionDefinitions : FALLBACK_SESSION_DEFINITIONS
              }
              onApplyPreset={handleApplyPreset}
              onSavePreset={handleSavePreset}
            />
          )}
          {step === 'confirm' && (
            <ConfirmStep
              form={form}
              models={models}
              integrations={integrations}
              sessionDefinitions={
                sessionDefinitions.length > 0 ? sessionDefinitions : FALLBACK_SESSION_DEFINITIONS
              }
              targets={targets}
            />
          )}
          {step === 'booting' && <BootingStep bootStep={bootStep} progress={bootProgress} />}
          {launchError ? (
            <div
              className="niuu:rounded niuu:border niuu:border-danger niuu:bg-bg-secondary niuu:px-3 niuu:py-2 niuu:text-xs niuu:text-danger"
              data-testid="wizard-error"
            >
              {launchError}
            </div>
          ) : null}

          {/* Footer */}
          <div className="niuu:flex niuu:items-center niuu:justify-between niuu:pt-4 niuu:border-t niuu:border-border-subtle">
            {canGoBack ? (
              <button
                className="niuu:rounded niuu:px-4 niuu:py-2 niuu:text-sm niuu:text-text-secondary niuu:hover:text-text-primary"
                onClick={handleBack}
                data-testid="wizard-back"
              >
                back
              </button>
            ) : (
              <div />
            )}
            {step === 'booting' ? (
              <button
                className="niuu:py-1 niuu:px-3 niuu:bg-brand niuu:text-bg-primary niuu:border niuu:border-brand niuu:rounded-sm niuu:cursor-pointer niuu:font-mono niuu:text-xs niuu:disabled:opacity-50"
                disabled={bootProgress < 1 || !createdSessionId || launching}
                onClick={() => {
                  if (!createdSessionId) return;
                  onOpenChange(false);
                  void navigate({
                    to: '/volundr/sessions/$sessionId',
                    params: { sessionId: createdSessionId },
                  });
                }}
                data-testid="wizard-open-pod"
              >
                {launching || bootProgress < 1 || !createdSessionId
                  ? 'booting\u2026'
                  : 'open pod \u2192'}
              </button>
            ) : (
              <button
                className="niuu:py-1 niuu:px-3 niuu:bg-brand niuu:text-bg-primary niuu:border niuu:border-brand niuu:rounded-sm niuu:cursor-pointer niuu:font-mono niuu:text-xs niuu:disabled:opacity-50"
                onClick={handleNext}
                disabled={isLastStep && !canLaunch}
                data-testid="wizard-next"
              >
                {isLastStep ? 'forge session' : 'continue \u2192'}
              </button>
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
