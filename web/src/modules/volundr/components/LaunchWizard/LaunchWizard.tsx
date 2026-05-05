import { useState, useCallback, useEffect } from 'react';
import { ChevronLeft, ChevronRight, Rocket } from 'lucide-react';
import type {
  VolundrPreset,
  VolundrTemplate,
  VolundrRepo,
  VolundrModel,
  McpServerConfig,
  TrackerIssue,
  SessionSource,
  MountMapping,
} from '@/modules/volundr/models';
import type { IVolundrService } from '@/modules/volundr/ports';
import { validateSessionName } from '@/utils/sessionName';
import { WizardStepper } from './WizardStepper';
import { TemplateStep, BLANK_TEMPLATE } from './steps/TemplateStep';
import { ConfigureStep } from './steps/ConfigureStep';
import { ReviewStep } from './steps/ReviewStep';
import styles from './LaunchWizard.module.css';

export interface LaunchConfig {
  name: string;
  source: SessionSource;
  model: string;
  templateName?: string;
  presetId?: string;
  taskType?: string;
  definition?: string;
  trackerIssue?: TrackerIssue;
  terminalRestricted?: boolean;
  workspaceId?: string;
  credentialNames?: string[];
  integrationIds?: string[];
  resourceConfig?: Record<string, string | undefined>;
  systemPrompt?: string;
  initialPrompt?: string;
}

export type SourceType = 'git' | 'local_mount';

export interface WizardState {
  template: VolundrTemplate;
  preset: VolundrPreset | null;
  name: string;
  sourceType: SourceType;
  repo: string;
  branch: string;
  mountPaths: MountMapping[];
  model: string;
  taskType: string;
  definition: string;
  trackerIssue?: TrackerIssue;
  workspaceId?: string;
  terminalRestricted: boolean;
  mcpServers: McpServerConfig[];
  resourceConfig: Record<string, string | undefined>;
  envVars: Record<string, string>;
  systemPrompt: string;
  initialPrompt: string;
  setupScripts: string[];
  selectedCredentials: string[];
  selectedIntegrations: string[];
  yamlMode: boolean;
  yamlContent: string;
}

export interface LaunchWizardProps {
  templates: VolundrTemplate[];
  presets: VolundrPreset[];
  repos: VolundrRepo[];
  models: Record<string, VolundrModel>;
  availableMcpServers: McpServerConfig[];
  availableSecrets: string[];
  service: IVolundrService;
  onLaunch: (config: LaunchConfig) => Promise<void>;
  onSaveTemplate: (template: VolundrTemplate) => Promise<void>;
  onSavePreset: (
    preset: Omit<VolundrPreset, 'id' | 'createdAt' | 'updatedAt'> & { id?: string }
  ) => Promise<VolundrPreset>;
  isLaunching: boolean;
  searchTrackerIssues?: (query: string) => Promise<TrackerIssue[]>;
}

const STEPS = ['Choose Template', 'Configure', 'Review & Launch'];

function buildInitialState(template: VolundrTemplate, repos: VolundrRepo[]): WizardState {
  let repo = '';
  let branch = '';

  if (template.repos.length > 0) {
    const tmplRepo = template.repos[0];
    const matched = repos.find(r => r.cloneUrl === tmplRepo.repo);
    if (matched) {
      repo = matched.cloneUrl;
      branch = tmplRepo.branch ?? matched.defaultBranch;
    }
  }

  return {
    template,
    preset: null,
    name: '',
    sourceType: 'git',
    repo,
    branch,
    mountPaths: [{ host_path: '', mount_path: '', read_only: true }],
    model: template.model ?? '',
    taskType: `skuld-${template.cliTool}`,
    definition: template.cliTool === 'codex' ? 'skuldCodex' : 'skuldClaude',
    mcpServers: [...template.mcpServers],
    resourceConfig: { ...template.resourceConfig },
    envVars: { ...template.envVars },
    systemPrompt: template.systemPrompt ?? '',
    initialPrompt: '',
    setupScripts: [...template.setupScripts],
    terminalRestricted: false,
    selectedCredentials: [...template.envSecretRefs],
    selectedIntegrations: [],
    yamlMode: false,
    yamlContent: '',
  };
}

export function LaunchWizard(props: LaunchWizardProps) {
  const {
    templates,
    presets,
    repos,
    models,
    availableMcpServers,
    availableSecrets,
    service,
    onLaunch,
    onSavePreset,
    isLaunching,
    searchTrackerIssues,
  } = props;
  const [step, setStep] = useState(1);
  const [state, setState] = useState<WizardState | null>(null);
  const [localMountsEnabled, setLocalMountsEnabled] = useState(false);
  const [miniMode, setMiniMode] = useState(false);

  useEffect(() => {
    service.getFeatures().then(f => {
      setLocalMountsEnabled(f.localMountsEnabled);
      setMiniMode(f.miniMode);
    });
  }, [service]);

  const handleTemplateSelect = useCallback(
    (template: VolundrTemplate | null) => {
      const chosen = template ?? BLANK_TEMPLATE;
      const initial = buildInitialState(chosen, repos);
      // Default to local_mount when local mounts are enabled and no repos are configured.
      if (localMountsEnabled && repos.length === 0) {
        initial.sourceType = 'local_mount';
      }
      setState(initial);
      setStep(2);
    },
    [repos, localMountsEnabled]
  );

  const updateState = useCallback((updates: Partial<WizardState>) => {
    setState(prev => (prev ? { ...prev, ...updates } : prev));
  }, []);

  const handleBack = useCallback(() => {
    if (step === 2) {
      setState(null);
      setStep(1);
      return;
    }
    setStep(prev => Math.max(1, prev - 1));
  }, [step]);

  const handleNext = useCallback(() => {
    setStep(prev => Math.min(3, prev + 1));
  }, []);

  const handleLaunch = useCallback(async () => {
    if (!state) {
      return;
    }

    const source: SessionSource =
      state.sourceType === 'local_mount'
        ? {
            type: 'local_mount',
            local_path: state.mountPaths[0]?.host_path ?? '',
            paths: state.mountPaths.filter(p => p.host_path && p.mount_path),
          }
        : { type: 'git', repo: state.repo, branch: state.branch };

    // Build resource config, filtering out empty values
    const resourceConfig = Object.fromEntries(
      Object.entries(state.resourceConfig).filter(([, v]) => v !== undefined && v !== '')
    );

    await onLaunch({
      name: state.name.trim(),
      source,
      model: state.model,
      templateName: state.template.name || undefined,
      presetId: state.preset?.id,
      taskType: state.taskType || undefined,
      definition: state.definition || 'skuldClaude',
      trackerIssue: state.trackerIssue,
      terminalRestricted: state.terminalRestricted,
      workspaceId: state.workspaceId,
      credentialNames: state.selectedCredentials.length ? state.selectedCredentials : undefined,
      integrationIds: state.selectedIntegrations.length ? state.selectedIntegrations : undefined,
      resourceConfig: Object.keys(resourceConfig).length > 0 ? resourceConfig : undefined,
      systemPrompt: state.systemPrompt || undefined,
      initialPrompt: state.initialPrompt || undefined,
    });
  }, [state, onLaunch]);

  const hasValidSource =
    state !== null &&
    (state.sourceType === 'git' ? state.repo !== '' : state.mountPaths.some(p => p.host_path));

  const canProceedToStep3 =
    state !== null &&
    state.name.trim() !== '' &&
    !validateSessionName(state.name.trim()) &&
    hasValidSource &&
    state.model !== '';

  const canLaunch = canProceedToStep3 && !isLaunching;

  return (
    <div className={styles.wizard}>
      <WizardStepper currentStep={step} steps={STEPS} />

      <div className={styles.content}>
        {step === 1 && <TemplateStep templates={templates} onSelect={handleTemplateSelect} />}

        {step === 2 && state && (
          <ConfigureStep
            state={state}
            presets={presets}
            repos={repos}
            models={models}
            availableMcpServers={availableMcpServers}
            availableSecrets={availableSecrets}
            service={service}
            searchTrackerIssues={searchTrackerIssues}
            localMountsEnabled={localMountsEnabled}
            miniMode={miniMode}
            onChange={updateState}
            onSavePreset={onSavePreset}
          />
        )}

        {step === 3 && state && <ReviewStep state={state} repos={repos} models={models} />}
      </div>

      {step > 1 && (
        <div className={styles.footer}>
          <button
            className={styles.backButton}
            onClick={handleBack}
            type="button"
            disabled={isLaunching}
          >
            <ChevronLeft className={styles.buttonIcon} />
            Back
          </button>

          <div className={styles.footerRight}>
            {step === 2 && (
              <button
                className={styles.nextButton}
                onClick={handleNext}
                type="button"
                disabled={!canProceedToStep3}
              >
                Next
                <ChevronRight className={styles.buttonIcon} />
              </button>
            )}

            {step === 3 && (
              <button
                className={styles.launchButton}
                onClick={handleLaunch}
                type="button"
                disabled={!canLaunch}
              >
                <Rocket className={styles.buttonIcon} />
                {isLaunching ? 'Launching...' : 'Launch Session'}
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export type { WizardState as LaunchWizardState };
