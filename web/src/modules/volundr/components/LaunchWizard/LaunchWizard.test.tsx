import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { vi, describe, it, expect, beforeEach } from 'vitest';
import { LaunchWizard } from './LaunchWizard';
import type {
  VolundrTemplate,
  VolundrRepo,
  VolundrModel,
  McpServerConfig,
} from '@/modules/volundr/models';

const baseTemplate: VolundrTemplate = {
  name: '',
  description: '',
  isDefault: false,
  repos: [],
  setupScripts: [],
  workspaceLayout: {},
  cliTool: 'claude',
  workloadType: 'coding',
  model: null,
  systemPrompt: null,
  resourceConfig: {},
  mcpServers: [],
  envVars: {},
  envSecretRefs: [],
  workloadConfig: {},
  terminalSidecar: { enabled: false, allowedCommands: [] },
  skills: [],
  rules: [],
};

const mockTemplates: VolundrTemplate[] = [
  {
    ...baseTemplate,
    name: 'Standard',
    description: 'Standard coding template',
    isDefault: true,
    model: 'claude-sonnet',
    repos: [{ repo: 'https://github.com/org/repo.git', branch: 'develop' }],
  },
  {
    ...baseTemplate,
    name: 'Minimal',
    description: 'Minimal template',
  },
];

const mockRepos: VolundrRepo[] = [
  {
    provider: 'github',
    org: 'org',
    name: 'repo',
    cloneUrl: 'https://github.com/org/repo.git',
    url: 'https://github.com/org/repo',
    defaultBranch: 'main',
    branches: ['main', 'develop'],
  },
];

const mockModels: Record<string, VolundrModel> = {
  'claude-sonnet': {
    name: 'Claude Sonnet',
    provider: 'cloud',
    tier: 'balanced',
    color: '#f59e0b',
    cost: '$3/MTok',
  },
};

const mockMcpServers: McpServerConfig[] = [
  { name: 'filesystem', type: 'stdio', command: 'mcp-fs' },
];

const mockService = {
  listWorkspaces: vi.fn().mockResolvedValue([]),
  getCredentials: vi.fn().mockResolvedValue([]),
  getIntegrations: vi.fn().mockResolvedValue([]),
  getFeatures: vi.fn().mockResolvedValue({ localMountsEnabled: false, miniMode: false }),
  getClusterResources: vi.fn().mockResolvedValue({ resourceTypes: [], nodes: [] }),
  getSessionDefinitions: vi.fn().mockResolvedValue([
    {
      key: 'skuldClaude',
      displayName: 'Claude Code',
      description: 'Anthropic Claude CLI agent',
      labels: ['claude'],
      defaultModel: 'claude-sonnet',
    },
    {
      key: 'skuldCodex',
      displayName: 'Codex',
      description: 'OpenAI Codex CLI agent',
      labels: ['codex'],
      defaultModel: 'codex',
    },
  ]),
} as unknown as import('@/ports').IVolundrService;

const defaultProps = {
  templates: mockTemplates,
  presets: [] as import('@/models').VolundrPreset[],
  repos: mockRepos,
  models: mockModels,
  availableMcpServers: mockMcpServers,
  availableSecrets: ['GITHUB_TOKEN'],
  service: mockService,
  onLaunch: vi.fn().mockResolvedValue(undefined),
  onSaveTemplate: vi.fn().mockResolvedValue(undefined),
  onSavePreset: vi.fn().mockResolvedValue({
    id: 'p1',
    name: 'test',
    templateName: '',
    config: {},
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
  }),
  isLaunching: false,
};

describe('LaunchWizard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    defaultProps.isLaunching = false;
    defaultProps.onLaunch = vi.fn().mockResolvedValue(undefined);
    defaultProps.onSaveTemplate = vi.fn().mockResolvedValue(undefined);
    defaultProps.onSavePreset = vi.fn().mockResolvedValue({
      id: 'p1',
      name: 'test',
      templateName: '',
      config: {},
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    });
  });

  describe('step 1 - template selection', () => {
    it('renders wizard stepper with 3 steps', () => {
      render(<LaunchWizard {...defaultProps} />);

      expect(screen.getByText('Choose Template')).toBeInTheDocument();
      expect(screen.getByText('Configure')).toBeInTheDocument();
      expect(screen.getByText('Review & Launch')).toBeInTheDocument();
    });

    it('starts on step 1 with template grid', () => {
      render(<LaunchWizard {...defaultProps} />);

      expect(screen.getByText('Standard')).toBeInTheDocument();
      expect(screen.getByText('Minimal')).toBeInTheDocument();
      expect(screen.getByText('Blank')).toBeInTheDocument();
    });

    it('does not show back/next buttons on step 1', () => {
      render(<LaunchWizard {...defaultProps} />);

      expect(screen.queryByText('Back')).not.toBeInTheDocument();
      expect(screen.queryByText('Next')).not.toBeInTheDocument();
    });

    it('advances to step 2 when a template is selected', () => {
      render(<LaunchWizard {...defaultProps} />);

      fireEvent.click(screen.getByText('Standard'));

      // Should now show step 2 content and Back button
      expect(screen.getByText('Back')).toBeInTheDocument();
    });

    it('advances to step 2 when blank is selected', () => {
      render(<LaunchWizard {...defaultProps} />);

      fireEvent.click(screen.getByText('Blank'));

      expect(screen.getByText('Back')).toBeInTheDocument();
    });
  });

  describe('navigation', () => {
    it('goes back to step 1 from step 2', () => {
      render(<LaunchWizard {...defaultProps} />);

      // Go to step 2
      fireEvent.click(screen.getByText('Standard'));
      expect(screen.getByText('Back')).toBeInTheDocument();

      // Go back
      fireEvent.click(screen.getByText('Back'));

      // Should be on step 1 again
      expect(screen.getByText('Blank')).toBeInTheDocument();
      expect(screen.queryByText('Back')).not.toBeInTheDocument();
    });

    it('shows Next button on step 2', () => {
      render(<LaunchWizard {...defaultProps} />);

      fireEvent.click(screen.getByText('Standard'));

      expect(screen.getByText('Next')).toBeInTheDocument();
    });

    it('disables Back button while launching', () => {
      render(<LaunchWizard {...defaultProps} isLaunching={true} />);

      fireEvent.click(screen.getByText('Standard'));

      expect(screen.getByText('Back')).toBeDisabled();
    });
  });

  describe('step 3 - review & launch', () => {
    function goToStep3() {
      render(<LaunchWizard {...defaultProps} />);
      // Select Standard template — pre-populates model and repo
      fireEvent.click(screen.getByText('Standard'));
      // Fill session name (required)
      fireEvent.change(screen.getByPlaceholderText('e.g. feature-auth-refactor'), {
        target: { value: 'test-session' },
      });
      // Model and repo are pre-selected from the Standard template
      // Click Next to go to step 3
      fireEvent.click(screen.getByText('Next'));
    }

    it('shows Launch Session button on step 3', () => {
      goToStep3();
      expect(screen.getByText('Launch Session')).toBeInTheDocument();
    });

    it('shows Launching... text when isLaunching', () => {
      defaultProps.isLaunching = true;
      render(<LaunchWizard {...defaultProps} />);
      fireEvent.click(screen.getByText('Standard'));
      fireEvent.change(screen.getByPlaceholderText('e.g. feature-auth-refactor'), {
        target: { value: 'test-session' },
      });
      fireEvent.click(screen.getByText('Next'));
      expect(screen.getByText('Launching...')).toBeInTheDocument();
    });

    it('calls onLaunch when Launch Session is clicked', async () => {
      goToStep3();
      fireEvent.click(screen.getByText('Launch Session'));

      expect(defaultProps.onLaunch).toHaveBeenCalledWith(
        expect.objectContaining({
          name: 'test-session',
          model: 'claude-sonnet',
          source: { type: 'git', repo: 'https://github.com/org/repo.git', branch: 'develop' },
          definition: 'skuldClaude',
          terminalRestricted: false,
        })
      );
    });

    it('includes resourceConfig when resource fields are set', async () => {
      render(<LaunchWizard {...defaultProps} />);
      fireEvent.click(screen.getByText('Standard'));
      fireEvent.change(screen.getByPlaceholderText('e.g. feature-auth-refactor'), {
        target: { value: 'test-session' },
      });
      // Open advanced config and set CPU
      fireEvent.click(screen.getByText('Advanced Configuration'));
      const cpuInput = screen.getByPlaceholderText('e.g. 4');
      fireEvent.change(cpuInput, { target: { value: '8' } });
      // Go to step 3 and launch
      fireEvent.click(screen.getByText('Next'));
      fireEvent.click(screen.getByText('Launch Session'));

      expect(defaultProps.onLaunch).toHaveBeenCalledWith(
        expect.objectContaining({
          resourceConfig: expect.objectContaining({ cpu: '8' }),
        })
      );
    });

    it('goes back to step 2 from step 3', () => {
      goToStep3();
      fireEvent.click(screen.getByText('Back'));
      // Should be on step 2 (Next button visible, no Launch button)
      expect(screen.getByText('Next')).toBeInTheDocument();
      expect(screen.queryByText('Launch Session')).not.toBeInTheDocument();
    });
  });

  describe('wizard stepper visual states', () => {
    it('step 1 is marked as current initially', () => {
      render(<LaunchWizard {...defaultProps} />);

      const nav = screen.getByRole('navigation', { name: 'Wizard steps' });
      const currentStep = nav.querySelector('[aria-current="step"]');
      expect(currentStep).toBeInTheDocument();
      expect(currentStep?.textContent).toContain('Choose Template');
    });

    it('step 2 is marked as current after selecting a template', () => {
      render(<LaunchWizard {...defaultProps} />);

      fireEvent.click(screen.getByText('Standard'));

      const nav = screen.getByRole('navigation', { name: 'Wizard steps' });
      const currentStep = nav.querySelector('[aria-current="step"]');
      expect(currentStep?.textContent).toContain('Configure');
    });
  });

  describe('mini mode with local mounts', () => {
    it('defaults to local_mount source when localMountsEnabled and no repos', async () => {
      const miniService = {
        ...mockService,
        getFeatures: vi.fn().mockResolvedValue({ localMountsEnabled: true, miniMode: true }),
        getSessionDefinitions: vi.fn().mockResolvedValue([]),
      } as unknown as import('@/ports').IVolundrService;

      render(<LaunchWizard {...defaultProps} repos={[]} service={miniService} />);

      // Select blank template to go to step 2.
      const blankBtn = await screen.findByText(/^Blank$/);
      await userEvent.click(blankBtn);

      // Wait for features to load and source type to be set.
      await vi.waitFor(() => {
        // The source type should default to local_mount since no repos and local mounts enabled.
        const projectDirInput = screen.queryByPlaceholderText(/absolute path/i);
        expect(projectDirInput).toBeInTheDocument();
      });
    });
  });
});
