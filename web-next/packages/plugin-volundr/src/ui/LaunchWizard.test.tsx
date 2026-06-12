import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { createMockBifrostService } from '@niuulabs/plugin-bifrost';
import { LaunchWizard } from './LaunchWizard';
import { createMockVolundrService } from '../adapters/mock';

const navigate = vi.fn();

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => navigate,
}));

function wrap(open = true, onOpenChange = vi.fn(), service = createMockVolundrService()) {
  return wrapWithServices(open, onOpenChange, service, {
    getRepos: service.getRepos.bind(service),
    getBranches: async () => [],
  });
}

function wrapWithServices(
  open = true,
  onOpenChange = vi.fn(),
  service = createMockVolundrService(),
  repoService: {
    getRepos: () => Promise<unknown>;
    getBranches: (repoUrl: string) => Promise<string[]>;
  },
  initialLaunchSpecRef?: string,
) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ServicesProvider
        services={{
          bifrost: createMockBifrostService(),
          volundr: service,
          'niuu.repos': repoService,
        }}
      >
        <LaunchWizard
          open={open}
          onOpenChange={onOpenChange}
          initialLaunchSpecRef={initialLaunchSpecRef}
        />
      </ServicesProvider>
    </QueryClientProvider>,
  );
}

async function waitForSourceStep() {
  await screen.findByTestId('step-source-content');
}

async function advanceToRuntime() {
  await waitForSourceStep();
  fireEvent.click(screen.getByTestId('wizard-next'));
  await screen.findByTestId('step-runtime-content');
}

async function advanceToConfirm() {
  await advanceToRuntime();
  fireEvent.click(screen.getByTestId('wizard-next'));
  await screen.findByTestId('step-confirm-content');
}

describe('LaunchWizard', () => {
  it('renders when open', async () => {
    wrap();
    await waitFor(() => expect(screen.getByText('Launch pod')).toBeInTheDocument());
  });

  it('shows step indicator with 3 steps', async () => {
    wrap();
    await waitFor(() => expect(screen.getByTestId('step-indicator')).toBeInTheDocument());
    expect(screen.getByText('Source')).toBeInTheDocument();
    expect(screen.getByText('Runtime')).toBeInTheDocument();
    expect(screen.getByText('Confirm')).toBeInTheDocument();
  });

  it('shows source step content initially', async () => {
    wrap();
    await waitFor(() => expect(screen.getByTestId('step-source-content')).toBeInTheDocument());
    expect(screen.getByText('Workspace source')).toBeInTheDocument();
  });

  it('navigates to runtime step on continue', async () => {
    wrap();
    await waitForSourceStep();
    fireEvent.click(screen.getByTestId('wizard-next'));
    expect(await screen.findByTestId('step-runtime-content')).toBeInTheDocument();
  });

  it('navigates back from runtime to source', async () => {
    wrap();
    await advanceToRuntime();
    fireEvent.click(screen.getByTestId('wizard-back'));
    expect(await screen.findByTestId('step-source-content')).toBeInTheDocument();
  });

  it('shows source type tabs', async () => {
    wrap();
    await waitForSourceStep();
    expect(screen.getByTestId('source-tab-git')).toBeInTheDocument();
    expect(screen.getByTestId('source-tab-local_mount')).toBeInTheDocument();
    expect(screen.getByTestId('source-tab-blank')).toBeInTheDocument();
  });

  it('shows runtime step with CLI options', async () => {
    wrap();
    await advanceToRuntime();
    expect(screen.getByTestId('runtime-option-skuldClaude')).toBeInTheDocument();
    expect(screen.getByTestId('runtime-option-skuldClaudeInteractive')).toBeInTheDocument();
    expect(screen.getByTestId('runtime-option-skuldCodex')).toBeInTheDocument();
  });

  it('shows confirm step with review rows', async () => {
    wrap();
    await advanceToConfirm();
    expect(screen.getAllByTestId('confirm-row').length).toBeGreaterThan(0);
    expect(screen.getByText('github.com/niuulabs/volundr@main')).toBeInTheDocument();
  });

  it('shows tracker search results from the service', async () => {
    wrap();
    await waitForSourceStep();

    fireEvent.change(screen.getByLabelText('Tracker issue (optional)'), {
      target: { value: 'NIU' },
    });

    await waitFor(() => expect(screen.getByText('NIU-801')).toBeInTheDocument());
    expect(screen.getByText('Hook tracker issue launch context into sessions')).toBeInTheDocument();
  });

  it('clears tracker results again when the query becomes too short', async () => {
    wrap();
    await waitForSourceStep();

    fireEvent.change(screen.getByLabelText('Tracker issue (optional)'), {
      target: { value: 'NIU' },
    });
    await screen.findByText('NIU-801');

    fireEvent.change(screen.getByLabelText('Tracker issue (optional)'), {
      target: { value: 'N' },
    });

    await waitFor(() => {
      expect(screen.queryByText('NIU-801')).not.toBeInTheDocument();
    });
  });

  it('uses embedded branch lists, links tracker issues, clears them, and supports blank sources', async () => {
    wrap();
    await waitForSourceStep();

    const branchSelect = (await screen.findByTestId('branch-select')) as HTMLSelectElement;
    expect(Array.from(branchSelect.options).map((option) => option.value)).toContain('main');
    expect(Array.from(branchSelect.options).map((option) => option.value)).toContain('develop');
    expect(Array.from(branchSelect.options).map((option) => option.value)).toContain(
      'feat/host-profiles',
    );

    fireEvent.change(screen.getByLabelText('Tracker issue (optional)'), {
      target: { value: 'NIU' },
    });
    await screen.findByText('NIU-801');

    fireEvent.click(screen.getByText('NIU-801'));
    expect(screen.getByText(/linked:/i)).toBeInTheDocument();
    expect(screen.getByDisplayValue('NIU-801')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'clear' }));
    await waitFor(() => {
      expect(screen.queryByText(/linked:/i)).not.toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId('source-tab-blank'));
    fireEvent.click(screen.getByTestId('wizard-next'));
    await screen.findByTestId('step-runtime-content');
    fireEvent.click(screen.getByTestId('wizard-next'));
    await screen.findByTestId('step-confirm-content');

    expect(screen.getByText('blank')).toBeInTheDocument();
  });

  it('loads manual branch options for repos without embedded branch lists', async () => {
    const service = createMockVolundrService();
    const repoService = {
      getRepos: vi.fn().mockResolvedValue([
        {
          provider: 'github',
          org: 'niuulabs',
          name: 'custom',
          cloneUrl: 'github.com/niuulabs/custom',
          url: 'https://github.com/niuulabs/custom',
          defaultBranch: 'main',
          branches: [],
        },
      ]),
      getBranches: vi.fn().mockResolvedValue(['main', 'release/1.0.0']),
    };

    wrapWithServices(true, vi.fn(), service, repoService);
    await waitForSourceStep();

    await waitFor(() => {
      expect(repoService.getBranches).toHaveBeenCalledWith('github.com/niuulabs/custom');
    });
    expect(screen.getByTestId('branch-select')).toBeInTheDocument();
  });

  it('starts booting on forge session click', async () => {
    const service = createMockVolundrService();
    const startSession = vi.spyOn(service, 'startSession');
    wrap(true, vi.fn(), service);
    await advanceToConfirm();
    fireEvent.click(screen.getByTestId('wizard-next'));
    await waitFor(() => expect(screen.getByTestId('step-booting-content')).toBeInTheDocument());
    await waitFor(() => {
      expect(startSession).toHaveBeenCalledWith(
        expect.objectContaining({
          name: 'main',
          source: { type: 'git', repo: 'github.com/niuulabs/volundr', branch: 'main' },
          model: 'claude-sonnet-4-6',
          definition: 'skuldClaude',
          taskType: 'skuld-claude',
          terminalRestricted: false,
          resourceConfig: { cpu: '2', memory: '8Gi' },
          workloadConfig: {},
        }),
      );
    });
    expect(screen.getAllByTestId('boot-step').length).toBe(8);
  });

  it('launches through a Forge tag selector', async () => {
    const service = createMockVolundrService();
    const startSession = vi.spyOn(service, 'startSession');
    wrap(true, vi.fn(), service);

    await advanceToRuntime();
    fireEvent.click(screen.getByText('Match tags'));
    fireEvent.click(screen.getByText('gpu'));
    fireEvent.click(screen.getByTestId('wizard-next'));
    await screen.findByTestId('step-confirm-content');
    expect(screen.getByText('tags(all): gpu')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('wizard-next'));
    await waitFor(() => expect(startSession).toHaveBeenCalled());
    expect(startSession).toHaveBeenCalledWith(
      expect.objectContaining({
        instanceId: undefined,
        targetTags: ['gpu'],
        targetMatch: 'all',
      }),
    );
  });

  it('serializes advanced runtime settings into a preset before launch when needed', async () => {
    const service = createMockVolundrService();
    const saveLaunchSpec = vi.spyOn(service, 'saveLaunchSpec');
    const startSession = vi.spyOn(service, 'startSession');
    wrap(true, vi.fn(), service);

    await advanceToRuntime();

    fireEvent.click(screen.getByText('show advanced'));
    fireEvent.click(screen.getByText('filesystem'));
    fireEvent.click(screen.getByText('add env var'));

    const envInputs = screen.getAllByPlaceholderText(/KEY|value/);
    fireEvent.change(envInputs[0]!, { target: { value: 'LOG_LEVEL' } });
    fireEvent.change(envInputs[1]!, { target: { value: 'debug' } });

    fireEvent.click(screen.getByTestId('wizard-next'));
    await screen.findByTestId('step-confirm-content');
    fireEvent.click(screen.getByTestId('wizard-next'));

    await waitFor(() => expect(saveLaunchSpec).toHaveBeenCalledTimes(1));
    expect(startSession).toHaveBeenCalledWith(
      expect.objectContaining({
        launchSpecId: expect.stringMatching(/^spec-/),
      }),
    );
  });

  it('can switch advanced runtime settings into yaml mode', async () => {
    wrap();

    await advanceToRuntime();

    fireEvent.click(screen.getByText('show advanced'));
    fireEvent.click(screen.getByText('edit as yaml'));

    await waitFor(() => {
      const yamlEditor = screen.getByPlaceholderText('Launch spec YAML') as HTMLTextAreaElement;
      expect(yamlEditor.value).toContain('cli_tool: claude');
    });
  });

  it('round-trips yaml edits back into the runtime form after surfacing parse errors', async () => {
    wrap();

    await advanceToRuntime();

    fireEvent.click(screen.getByText('show advanced'));
    fireEvent.click(screen.getByText('edit as yaml'));

    const yamlEditor = screen.getByPlaceholderText('Launch spec YAML') as HTMLTextAreaElement;
    fireEvent.change(yamlEditor, {
      target: { value: 'cli_tool: [broken' },
    });
    fireEvent.click(screen.getByText('form view'));

    await waitFor(() => {
      expect(screen.getByPlaceholderText('Launch spec YAML')).toBeInTheDocument();
    });
    expect(screen.getByText(/unexpected end of the stream/i)).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText('Launch spec YAML'), {
      target: {
        value: `cli_tool: codex
model: gpt-5.5
system_prompt: Keep answers short.
resource_config:
  cpu: "3"
  memory: "12Gi"
  gpu: "1"
mcp_servers:
  - name: review-http
    type: http
    url: http://localhost:3010/mcp
env_vars:
  LOG_LEVEL: debug
env_secret_refs:
  - openai-key
integration_ids:
  - github-primary
setup_scripts:
  - pnpm lint
source:
  type: local_mount
  local_path: ~/code/niuu/custom
  paths:
    - host_path: ~/code/niuu/custom
      mount_path: /workspace
      read_only: false
`,
      },
    });
    fireEvent.click(screen.getByText('form view'));

    await waitFor(() => {
      expect(screen.queryByPlaceholderText('Launch spec YAML')).not.toBeInTheDocument();
    });

    expect(screen.getByDisplayValue('3')).toBeInTheDocument();
    expect(screen.getByDisplayValue('12Gi')).toBeInTheDocument();
    expect(screen.getByDisplayValue('1')).toBeInTheDocument();
    expect(screen.getByDisplayValue('Keep answers short.')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('wizard-next'));
    await screen.findByTestId('step-confirm-content');

    expect(screen.getByText('skuld-codex')).toBeInTheDocument();
    expect(screen.getByText('~/code/niuu/custom')).toBeInTheDocument();
    expect(screen.getByText('openai-key')).toBeInTheDocument();
    expect(screen.getByText('github-primary')).toBeInTheDocument();
    expect(screen.getByText('review-http')).toBeInTheDocument();
    expect(screen.getByText('LOG_LEVEL=debug')).toBeInTheDocument();
    expect(screen.getByText('pnpm lint')).toBeInTheDocument();
    expect(screen.getByText(/Keep answers short\./)).toBeInTheDocument();
  });

  it('applies a saved preset and can clear back to custom runtime settings', async () => {
    wrap();

    await advanceToRuntime();

    const presetField = screen.getByText('Load launch spec').closest('.niuu-field');
    const presetSelect = presetField?.querySelector('select') as HTMLSelectElement;
    fireEvent.change(presetSelect, { target: { value: 'spec-fast-review' } });

    fireEvent.click(screen.getByText('show advanced'));
    await waitFor(() => {
      const prompt = screen.getByPlaceholderText(
        'Override the default system prompt',
      ) as HTMLTextAreaElement;
      expect(prompt.value).toContain('Review changes and summarize the main risks.');
    });

    fireEvent.change(presetSelect, { target: { value: presetSelect.options[0]!.value } });
    await waitFor(() => {
      expect(screen.getByText(/No launch spec loaded/i)).toBeInTheDocument();
    });
  });

  it('applies an initial launch spec ref after loading presets', async () => {
    const service = createMockVolundrService();
    wrapWithServices(
      true,
      vi.fn(),
      service,
      {
        getRepos: service.getRepos.bind(service),
        getBranches: async () => [],
      },
      'spec-fast-review',
    );

    await advanceToRuntime();

    const presetField = screen.getByText('Load launch spec').closest('.niuu-field');
    const presetSelect = presetField?.querySelector('select') as HTMLSelectElement;
    expect(presetSelect.value).toBe('spec-fast-review');
    expect((screen.getByTestId('model-select') as HTMLSelectElement).value).toBe(
      'claude-sonnet-4-6',
    );

    fireEvent.click(screen.getByTestId('wizard-back'));
    await screen.findByTestId('step-source-content');
    expect((screen.getByTestId('repo-select') as HTMLSelectElement).value).toBe(
      'github.com/niuulabs/volundr',
    );
  });

  it('launches a catalog launch spec by name instead of treating it as a saved spec id', async () => {
    const service = createMockVolundrService();
    const startSession = vi.spyOn(service, 'startSession');
    wrap(true, vi.fn(), service);

    await advanceToRuntime();

    const presetField = screen.getByText('Load launch spec').closest('.niuu-field');
    const presetSelect = presetField?.querySelector('select') as HTMLSelectElement;
    fireEvent.change(presetSelect, { target: { value: 'standard-claude' } });

    fireEvent.click(screen.getByTestId('wizard-next'));
    await screen.findByTestId('step-confirm-content');
    fireEvent.click(screen.getByTestId('wizard-next'));

    await waitFor(() =>
      expect(startSession).toHaveBeenCalledWith(
        expect.objectContaining({
          launchSpec: 'standard-claude',
          launchSpecId: undefined,
        }),
      ),
    );
  });

  it('saves a preset from runtime settings and clears the save field', async () => {
    const service = createMockVolundrService();
    const saveLaunchSpec = vi.spyOn(service, 'saveLaunchSpec');
    wrap(true, vi.fn(), service);

    await advanceToRuntime();

    const presetName = screen.getByPlaceholderText('save as launch spec') as HTMLInputElement;
    fireEvent.change(presetName, { target: { value: 'pairing-preset' } });
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() => {
      expect(saveLaunchSpec).toHaveBeenCalledWith(
        expect.objectContaining({
          name: 'pairing-preset',
        }),
      );
    });
    expect(presetName.value).toBe('');
  });

  it('manages preset and custom mcp servers in advanced runtime settings', async () => {
    wrap();

    await advanceToRuntime();

    fireEvent.click(screen.getByText('show advanced'));

    fireEvent.click(screen.getByText('filesystem'));
    await screen.findByText('uvx mcp-filesystem /workspace');

    const filesystemCard = screen.getByText('filesystem').parentElement
      ?.parentElement as HTMLElement;
    fireEvent.click(within(filesystemCard).getByRole('button', { name: 'remove' }));
    await waitFor(() => {
      expect(screen.queryByText('uvx mcp-filesystem /workspace')).not.toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('add custom server'));
    fireEvent.change(screen.getByPlaceholderText('filesystem'), {
      target: { value: 'review-stdio' },
    });
    fireEvent.change(screen.getByPlaceholderText('uvx'), {
      target: { value: 'uvx' },
    });
    fireEvent.change(screen.getByPlaceholderText('mcp-filesystem /workspace'), {
      target: { value: 'mcp-review /workspace' },
    });
    fireEvent.change(screen.getByPlaceholderText('KEY'), {
      target: { value: 'TOKEN' },
    });
    fireEvent.change(screen.getByPlaceholderText('value'), {
      target: { value: 'secret' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'add' }));

    const envRow = screen.getByDisplayValue('TOKEN').parentElement as HTMLElement;
    fireEvent.click(within(envRow).getByRole('button', { name: 'remove' }));
    await waitFor(() => {
      expect(screen.queryByDisplayValue('TOKEN')).not.toBeInTheDocument();
    });

    fireEvent.change(screen.getByPlaceholderText('KEY'), {
      target: { value: 'TOKEN' },
    });
    fireEvent.change(screen.getByPlaceholderText('value'), {
      target: { value: 'secret' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'add' }));
    fireEvent.click(screen.getByRole('button', { name: 'add server' }));

    await screen.findByText('uvx mcp-review /workspace');

    fireEvent.click(screen.getByText('add custom server'));
    fireEvent.change(screen.getByPlaceholderText('filesystem'), {
      target: { value: 'review-http' },
    });
    fireEvent.change(screen.getByDisplayValue('stdio'), {
      target: { value: 'http' },
    });
    fireEvent.change(screen.getByPlaceholderText('http://localhost:3000/sse'), {
      target: { value: 'http://localhost:3010/mcp' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'add server' }));

    await screen.findByText('http://localhost:3010/mcp');

    const reviewHttpCard = screen.getByText('review-http').parentElement
      ?.parentElement as HTMLElement;
    fireEvent.click(within(reviewHttpCard).getByRole('button', { name: 'remove' }));

    await waitFor(() => {
      expect(screen.queryByText('http://localhost:3010/mcp')).not.toBeInTheDocument();
    });
  });

  it('navigates to the created session once booting completes', async () => {
    const onOpenChange = vi.fn();
    const service = createMockVolundrService();
    wrap(true, onOpenChange, service);

    await advanceToConfirm();
    fireEvent.click(screen.getByTestId('wizard-next'));

    await waitFor(() => expect(screen.getByTestId('step-booting-content')).toBeInTheDocument());
    await waitFor(() => expect(screen.getByTestId('wizard-open-pod')).not.toBeDisabled(), {
      timeout: 10000,
    });

    fireEvent.click(screen.getByTestId('wizard-open-pod'));

    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(navigate).toHaveBeenCalledWith({
      to: '/volundr/sessions/$sessionId',
      params: { sessionId: 'sess-new' },
    });
  }, 12000);

  it('prevents launch when requested CPU exceeds available cluster capacity', async () => {
    wrap();
    await advanceToRuntime();
    fireEvent.click(screen.getByText('show advanced'));

    fireEvent.change(screen.getByLabelText('CPU (cores)'), {
      target: { value: '999' },
    });

    fireEvent.click(screen.getByTestId('wizard-next'));
    expect(await screen.findByTestId('step-confirm-content')).toBeInTheDocument();
    expect(screen.getByTestId('wizard-next')).toBeDisabled();
  });

  it('returns to confirm with an error when launch fails', async () => {
    const service = createMockVolundrService();
    vi.spyOn(service, 'startSession').mockRejectedValue(new Error('Launch exploded'));
    wrap(true, vi.fn(), service);

    await advanceToConfirm();
    fireEvent.click(screen.getByTestId('wizard-next'));

    await waitFor(() => {
      expect(screen.getByTestId('step-confirm-content')).toBeInTheDocument();
    });
    expect(screen.getByText('Launch exploded')).toBeInTheDocument();
  });

  it('resets back to the source step when reopened', async () => {
    const onOpenChange = vi.fn();
    const service = createMockVolundrService();
    const view = wrapWithServices(true, onOpenChange, service, {
      getRepos: service.getRepos.bind(service),
      getBranches: async () => [],
    });

    await advanceToRuntime();
    expect(screen.getByTestId('step-runtime-content')).toBeInTheDocument();

    view.rerender(
      <QueryClientProvider
        client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
      >
        <ServicesProvider
          services={{
            bifrost: createMockBifrostService(),
            volundr: service,
            'niuu.repos': {
              getRepos: service.getRepos.bind(service),
              getBranches: async () => [],
            },
          }}
        >
          <LaunchWizard open={false} onOpenChange={onOpenChange} />
        </ServicesProvider>
      </QueryClientProvider>,
    );

    view.rerender(
      <QueryClientProvider
        client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
      >
        <ServicesProvider
          services={{
            bifrost: createMockBifrostService(),
            volundr: service,
            'niuu.repos': {
              getRepos: service.getRepos.bind(service),
              getBranches: async () => [],
            },
          }}
        >
          <LaunchWizard open onOpenChange={onOpenChange} />
        </ServicesProvider>
      </QueryClientProvider>,
    );

    expect(await screen.findByTestId('step-source-content')).toBeInTheDocument();
  });

  it('does not render when closed', () => {
    wrap(false);
    expect(screen.queryByText('Launch pod')).not.toBeInTheDocument();
  });
});
