import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { LaunchWizard } from './LaunchWizard';
import { createMockTemplateStore, createMockVolundrService } from '../adapters/mock';

const navigate = vi.fn();

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => navigate,
}));

function wrap(open = true, onOpenChange = vi.fn(), service = createMockVolundrService()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const templateStore = createMockTemplateStore();
  return render(
    <QueryClientProvider client={client}>
      <ServicesProvider
        services={{
          volundr: service,
          'volundr.templates': templateStore,
          'niuu.repos': { getRepos: service.getRepos.bind(service) },
        }}
      >
        <LaunchWizard open={open} onOpenChange={onOpenChange} />
      </ServicesProvider>
    </QueryClientProvider>,
  );
}

describe('LaunchWizard', () => {
  it('renders when open', async () => {
    wrap();
    await waitFor(() => expect(screen.getByText('Launch pod')).toBeInTheDocument());
  });

  it('shows step indicator with 4 steps', async () => {
    wrap();
    await waitFor(() => expect(screen.getByTestId('step-indicator')).toBeInTheDocument());
    expect(screen.getByText('Template')).toBeInTheDocument();
    expect(screen.getByText('Source')).toBeInTheDocument();
    expect(screen.getByText('Runtime')).toBeInTheDocument();
    expect(screen.getByText('Confirm')).toBeInTheDocument();
  });

  it('shows template step content initially', async () => {
    wrap();
    await waitFor(() => expect(screen.getByTestId('step-template-content')).toBeInTheDocument());
    expect(screen.getByText('Choose a template')).toBeInTheDocument();
  });

  it('navigates to source step on continue', async () => {
    wrap();
    await waitFor(() => expect(screen.getByTestId('wizard-next')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('wizard-next'));
    expect(screen.getByTestId('step-source-content')).toBeInTheDocument();
  });

  it('navigates back from source to template', async () => {
    wrap();
    await waitFor(() => fireEvent.click(screen.getByTestId('wizard-next')));
    expect(screen.getByTestId('step-source-content')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('wizard-back'));
    expect(screen.getByTestId('step-template-content')).toBeInTheDocument();
  });

  it('shows source type tabs', async () => {
    wrap();
    await waitFor(() => fireEvent.click(screen.getByTestId('wizard-next')));
    expect(screen.getByTestId('source-tab-git')).toBeInTheDocument();
    expect(screen.getByTestId('source-tab-local_mount')).toBeInTheDocument();
    expect(screen.getByTestId('source-tab-blank')).toBeInTheDocument();
  });

  it('shows runtime step with CLI options', async () => {
    wrap();
    // navigate to runtime (template -> source -> runtime)
    await waitFor(() => fireEvent.click(screen.getByTestId('wizard-next')));
    fireEvent.click(screen.getByTestId('wizard-next'));
    expect(screen.getByTestId('step-runtime-content')).toBeInTheDocument();
    expect(screen.getByTestId('cli-option-claude')).toBeInTheDocument();
    expect(screen.getByTestId('cli-option-codex')).toBeInTheDocument();
  });

  it('shows confirm step with review rows', async () => {
    wrap();
    // navigate to confirm
    await waitFor(() => fireEvent.click(screen.getByTestId('wizard-next')));
    fireEvent.click(screen.getByTestId('wizard-next'));
    fireEvent.click(screen.getByTestId('wizard-next'));
    expect(screen.getByTestId('step-confirm-content')).toBeInTheDocument();
    expect(screen.getAllByTestId('confirm-row').length).toBeGreaterThan(0);
    expect(screen.getByText('github.com/niuulabs/volundr@main')).toBeInTheDocument();
  });

  it('shows tracker search results from the service', async () => {
    wrap();
    await waitFor(() => fireEvent.click(screen.getByTestId('wizard-next')));

    fireEvent.change(screen.getByLabelText('Tracker issue (optional)'), {
      target: { value: 'NIU' },
    });

    await waitFor(() => expect(screen.getByText('NIU-801')).toBeInTheDocument());
    expect(screen.getByText('Hook tracker issue launch context into sessions')).toBeInTheDocument();
  });

  it('starts booting on forge session click', async () => {
    const service = createMockVolundrService();
    const startSession = vi.spyOn(service, 'startSession');
    wrap(true, vi.fn(), service);
    // navigate to confirm
    await waitFor(() => fireEvent.click(screen.getByTestId('wizard-next')));
    fireEvent.click(screen.getByTestId('wizard-next'));
    fireEvent.click(screen.getByTestId('wizard-next'));
    // Click forge
    fireEvent.click(screen.getByTestId('wizard-next'));
    await waitFor(() => expect(screen.getByTestId('step-booting-content')).toBeInTheDocument());
    await waitFor(() => {
      expect(startSession).toHaveBeenCalledWith(
        expect.objectContaining({
          name: 'main',
          source: { type: 'git', repo: 'github.com/niuulabs/volundr', branch: 'main' },
          model: 'sonnet-primary',
          templateName: 'niuu-platform',
          definition: 'skuld-claude',
          taskType: 'skuld-claude',
          terminalRestricted: true,
          resourceConfig: { cpu: '2', memory: '8Gi' },
          workloadConfig: {},
        }),
      );
    });
    expect(screen.getAllByTestId('boot-step').length).toBe(8);
  });

  it('serializes advanced runtime settings into a preset before launch when needed', async () => {
    const service = createMockVolundrService();
    const savePreset = vi.spyOn(service, 'savePreset');
    const startSession = vi.spyOn(service, 'startSession');
    wrap(true, vi.fn(), service);

    await waitFor(() => fireEvent.click(screen.getByTestId('wizard-next')));
    fireEvent.click(screen.getByTestId('wizard-next'));

    fireEvent.click(screen.getByText('show advanced'));
    fireEvent.click(screen.getByText('filesystem'));
    fireEvent.click(screen.getByText('add env var'));

    const envInputs = screen.getAllByPlaceholderText(/KEY|value/);
    fireEvent.change(envInputs[0]!, { target: { value: 'LOG_LEVEL' } });
    fireEvent.change(envInputs[1]!, { target: { value: 'debug' } });

    fireEvent.click(screen.getByTestId('wizard-next'));
    fireEvent.click(screen.getByTestId('wizard-next'));

    await waitFor(() => expect(savePreset).toHaveBeenCalledTimes(1));
    expect(startSession).toHaveBeenCalledWith(
      expect.objectContaining({
        presetId: expect.stringMatching(/^preset-/),
      }),
    );
  });

  it('can switch advanced runtime settings into yaml mode', async () => {
    wrap();

    await waitFor(() => fireEvent.click(screen.getByTestId('wizard-next')));
    fireEvent.click(screen.getByTestId('wizard-next'));

    fireEvent.click(screen.getByText('show advanced'));
    fireEvent.click(screen.getByText('edit as yaml'));

    await waitFor(() => {
      const yamlEditor = screen.getByPlaceholderText('Preset YAML') as HTMLTextAreaElement;
      expect(yamlEditor.value).toContain('cli_tool: claude');
    });
  });

  it('navigates to the created session once booting completes', async () => {
    const onOpenChange = vi.fn();
    const service = createMockVolundrService();
    wrap(true, onOpenChange, service);

    await waitFor(() => fireEvent.click(screen.getByTestId('wizard-next')));
    fireEvent.click(screen.getByTestId('wizard-next'));
    fireEvent.click(screen.getByTestId('wizard-next'));
    fireEvent.click(screen.getByTestId('wizard-next'));

    await waitFor(() => expect(screen.getByTestId('step-booting-content')).toBeInTheDocument());
    await waitFor(() => expect(screen.getByTestId('wizard-open-pod')).not.toBeDisabled(), {
      timeout: 10000,
    });

    fireEvent.click(screen.getByTestId('wizard-open-pod'));

    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(navigate).toHaveBeenCalledWith({
      to: '/volundr/session/$sessionId',
      params: { sessionId: 'sess-new' },
    });
  }, 12000);

  it('prevents launch when requested CPU exceeds available cluster capacity', async () => {
    wrap();
    await waitFor(() => fireEvent.click(screen.getByTestId('wizard-next')));
    fireEvent.click(screen.getByTestId('wizard-next'));

    fireEvent.change(screen.getByLabelText('CPU (cores)'), {
      target: { value: '999' },
    });

    fireEvent.click(screen.getByTestId('wizard-next'));
    expect(screen.getByTestId('step-confirm-content')).toBeInTheDocument();
    expect(screen.getByTestId('wizard-next')).toBeDisabled();
  });

  it('does not render when closed', () => {
    wrap(false);
    expect(screen.queryByText('Launch pod')).not.toBeInTheDocument();
  });
});
