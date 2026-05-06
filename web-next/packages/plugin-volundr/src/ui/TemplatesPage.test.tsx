import { describe, it, expect } from 'vitest';
import { screen, waitFor, fireEvent, within } from '@testing-library/react';
import { TemplatesPage } from './TemplatesPage';
import { renderWithVolundr } from '../testing/renderWithVolundr';
import { createMockTemplateStore } from '../adapters/mock';
import type { Template } from '../domain/template';
import type { ITemplateStore } from '../ports/ITemplateStore';

/** Helper: create a store pre-loaded with custom templates. */
function storeWith(templates: Template[]): ITemplateStore {
  return {
    ...createMockTemplateStore(),
    listTemplates: async () => templates,
  };
}

/** A rich template with mounts, env vars, secret refs, GPU, and tolerations. */
const RICH_TEMPLATE: Template = {
  id: 'tpl-rich',
  name: 'rich-template',
  version: 3,
  spec: {
    image: 'ghcr.io/niuulabs/forge',
    tag: '7.2',
    mounts: [
      {
        name: 'repo-volundr',
        mountPath: '/workspace',
        source: { kind: 'git', repo: 'niuu/volundr', branch: 'main' },
        readOnly: false,
      },
      {
        name: 'data-pvc',
        mountPath: '/data',
        source: { kind: 'pvc', name: 'shared-data' },
        readOnly: true,
      },
    ],
    env: { API_URL: 'https://api.niuu.world', NODE_ENV: 'production' },
    envSecretRefs: ['DB_PASSWORD'],
    tools: ['bash', 'python', 'git'],
    resources: {
      cpuRequest: '2',
      cpuLimit: '4',
      memRequestMi: 2048,
      memLimitMi: 4096,
      gpuCount: 2,
    },
    ttlSec: 7200,
    idleTimeoutSec: 1800,
    clusterAffinity: ['cl-eitri'],
    tolerations: ['gpu-only', 'spot'],
  },
  createdAt: '2026-03-01T00:00:00Z',
  updatedAt: '2026-04-01T00:00:00Z',
};

describe('TemplatesPage', () => {
  // -----------------------------------------------------------------------
  // Basic rendering
  // -----------------------------------------------------------------------

  it('renders the heading', () => {
    renderWithVolundr(<TemplatesPage />);
    return waitFor(() =>
      expect(screen.getByRole('heading', { name: /templates/i })).toBeInTheDocument(),
    );
  });

  it('renders the subtitle', () => {
    renderWithVolundr(<TemplatesPage />);
    return waitFor(() =>
      expect(screen.getByText(/workspace \+ runtime bundles/i)).toBeInTheDocument(),
    );
  });

  // -----------------------------------------------------------------------
  // Loading state
  // -----------------------------------------------------------------------

  it('shows loading state before templates resolve', () => {
    const slowStore = {
      ...createMockTemplateStore(),
      listTemplates: () => new Promise<never>(() => {}),
    };
    renderWithVolundr(<TemplatesPage />, { templateStore: slowStore });
    expect(screen.getByRole('status')).toBeInTheDocument();
  });

  // -----------------------------------------------------------------------
  // Error state
  // -----------------------------------------------------------------------

  it('shows error state when service throws', async () => {
    const failStore = {
      ...createMockTemplateStore(),
      listTemplates: async () => {
        throw new Error('template service down');
      },
    };
    renderWithVolundr(<TemplatesPage />, { templateStore: failStore });
    await waitFor(() => expect(screen.getByText('template service down')).toBeInTheDocument());
  });

  it('shows generic error message for non-Error throws', async () => {
    const failStore = {
      ...createMockTemplateStore(),
      listTemplates: async () => {
        throw 'unexpected';
      },
    };
    renderWithVolundr(<TemplatesPage />, { templateStore: failStore });
    await waitFor(() => expect(screen.getByText('failed to load templates')).toBeInTheDocument());
  });

  // -----------------------------------------------------------------------
  // Empty state
  // -----------------------------------------------------------------------

  it('shows empty state when no templates exist', async () => {
    const emptyStore = {
      ...createMockTemplateStore(),
      listTemplates: async () => [],
    };
    renderWithVolundr(<TemplatesPage />, { templateStore: emptyStore });
    await waitFor(() => expect(screen.getByTestId('empty-state')).toBeInTheDocument());
  });

  // -----------------------------------------------------------------------
  // Template list rendering
  // -----------------------------------------------------------------------

  it('renders template cards after data loads', async () => {
    renderWithVolundr(<TemplatesPage />);
    await waitFor(() => expect(screen.getAllByTestId('template-card').length).toBeGreaterThan(0));
  });

  it('renders both seed templates', async () => {
    renderWithVolundr(<TemplatesPage />);
    await waitFor(() => expect(screen.getByText('niuu-platform')).toBeInTheDocument());
    expect(screen.getAllByText('bifrost-gateway').length).toBeGreaterThan(0);
  });

  it('shows the template list as a list role', async () => {
    renderWithVolundr(<TemplatesPage />);
    await waitFor(() =>
      expect(screen.getByRole('list', { name: /pod templates/i })).toBeInTheDocument(),
    );
  });

  // -----------------------------------------------------------------------
  // Template selection
  // -----------------------------------------------------------------------

  it('auto-selects the showcase template', async () => {
    renderWithVolundr(<TemplatesPage />);
    await waitFor(() => expect(screen.getAllByTestId('template-card').length).toBeGreaterThan(0));
    const selected = screen
      .getAllByTestId('template-card')
      .find((card) => card.getAttribute('aria-pressed') === 'true');
    expect(selected).toBeTruthy();
    expect(within(selected!).getByText('bifrost-gateway')).toBeInTheDocument();
  });

  it('selects a template when clicked', async () => {
    renderWithVolundr(<TemplatesPage />);
    await waitFor(() => expect(screen.getAllByTestId('template-card').length).toBeGreaterThan(0));
    // Click the second card
    const cards = screen.getAllByTestId('template-card');
    fireEvent.click(cards[1]!);
    await waitFor(() => expect(cards[1]).toHaveAttribute('aria-pressed', 'true'));
  });

  it('shows detail panel for selected template', async () => {
    renderWithVolundr(<TemplatesPage />);
    await waitFor(() => expect(screen.getAllByTestId('template-card').length).toBeGreaterThan(0));
    // Detail panel should show overview tab by default
    expect(screen.getByTestId('tab-overview')).toBeInTheDocument();
  });

  // -----------------------------------------------------------------------
  // Tab navigation
  // -----------------------------------------------------------------------

  it('renders all 6 tabs', async () => {
    renderWithVolundr(<TemplatesPage />);
    await waitFor(() => expect(screen.getAllByTestId('template-card').length).toBeGreaterThan(0));
    const tabs = screen.getAllByRole('tab');
    expect(tabs).toHaveLength(6);
    expect(tabs.map((t) => t.textContent)).toEqual([
      'overview',
      'workspace',
      'runtime',
      'mcp',
      'skills',
      'rules',
    ]);
  });

  it('overview tab is active by default', async () => {
    renderWithVolundr(<TemplatesPage />);
    await waitFor(() => expect(screen.getAllByTestId('template-card').length).toBeGreaterThan(0));
    const overviewTab = screen.getByRole('tab', { name: /overview/i });
    expect(overviewTab).toHaveAttribute('aria-selected', 'true');
  });

  it('switches to workspace tab', async () => {
    renderWithVolundr(<TemplatesPage />);
    await waitFor(() => expect(screen.getAllByTestId('template-card').length).toBeGreaterThan(0));
    fireEvent.click(screen.getByRole('tab', { name: /workspace/i }));
    expect(screen.getByTestId('tab-workspace')).toBeInTheDocument();
  });

  it('switches to runtime tab', async () => {
    renderWithVolundr(<TemplatesPage />);
    await waitFor(() => expect(screen.getAllByTestId('template-card').length).toBeGreaterThan(0));
    fireEvent.click(screen.getByRole('tab', { name: /runtime/i }));
    expect(screen.getByTestId('tab-runtime')).toBeInTheDocument();
  });

  it('switches to mcp tab', async () => {
    renderWithVolundr(<TemplatesPage />);
    await waitFor(() => expect(screen.getAllByTestId('template-card').length).toBeGreaterThan(0));
    fireEvent.click(screen.getByRole('tab', { name: /^mcp$/i }));
    expect(screen.getByTestId('tab-mcp')).toBeInTheDocument();
  });

  it('switches to skills tab', async () => {
    renderWithVolundr(<TemplatesPage />);
    await waitFor(() => expect(screen.getAllByTestId('template-card').length).toBeGreaterThan(0));
    fireEvent.click(screen.getByRole('tab', { name: /skills/i }));
    expect(screen.getByTestId('tab-skills')).toBeInTheDocument();
  });

  it('switches to rules tab', async () => {
    renderWithVolundr(<TemplatesPage />);
    await waitFor(() => expect(screen.getAllByTestId('template-card').length).toBeGreaterThan(0));
    fireEvent.click(screen.getByRole('tab', { name: /rules/i }));
    expect(screen.getByTestId('tab-rules')).toBeInTheDocument();
  });

  // -----------------------------------------------------------------------
  // Tab content: Overview
  // -----------------------------------------------------------------------

  it('overview tab shows detail cards', async () => {
    renderWithVolundr(<TemplatesPage />);
    await waitFor(() => expect(screen.getByTestId('tab-overview')).toBeInTheDocument());
    const cards = screen.getAllByTestId('detail-card');
    expect(cards.length).toBeGreaterThanOrEqual(4);
  });

  it('overview tab shows cli/model info in detail cards', async () => {
    renderWithVolundr(<TemplatesPage />);
    await waitFor(() => expect(screen.getByTestId('tab-overview')).toBeInTheDocument());
    expect(screen.getByText('codex')).toBeInTheDocument();
    expect(screen.getByText('codex-primary')).toBeInTheDocument();
  });

  // -----------------------------------------------------------------------
  // Tab content: Workspace
  // -----------------------------------------------------------------------

  it('workspace tab shows empty state for templates with no mounts', async () => {
    renderWithVolundr(<TemplatesPage />);
    await waitFor(() => expect(screen.getAllByTestId('template-card').length).toBeGreaterThan(0));
    fireEvent.click(screen.getByRole('button', { name: /niuu-platform/i }));
    fireEvent.click(screen.getByRole('tab', { name: /workspace/i }));
    expect(screen.getByText(/blank · no sources/i)).toBeInTheDocument();
  });

  // -----------------------------------------------------------------------
  // Tab content: Runtime
  // -----------------------------------------------------------------------

  it('runtime tab shows image and shell details', async () => {
    renderWithVolundr(<TemplatesPage />);
    await waitFor(() => expect(screen.getAllByTestId('template-card').length).toBeGreaterThan(0));
    fireEvent.click(screen.getByRole('tab', { name: /runtime/i }));
    expect(screen.getByText('base')).toBeInTheDocument();
    expect(screen.getByText('shell')).toBeInTheDocument();
  });

  it('runtime tab shows TTL and idle timeout', async () => {
    renderWithVolundr(<TemplatesPage />);
    await waitFor(() => expect(screen.getAllByTestId('template-card').length).toBeGreaterThan(0));
    fireEvent.click(screen.getByRole('tab', { name: /runtime/i }));
    expect(screen.getByText('60m')).toBeInTheDocument(); // 3600s = 60m
    expect(screen.getByText('10m')).toBeInTheDocument(); // 600s = 10m
  });

  // -----------------------------------------------------------------------
  // Template list card: description + usage count
  // -----------------------------------------------------------------------

  it('renders description in the detail header', async () => {
    renderWithVolundr(<TemplatesPage />);
    await waitFor(() => expect(screen.getAllByTestId('template-card').length).toBeGreaterThan(0));
    expect(screen.getByText(/provider adapters/i)).toBeInTheDocument();
  });

  it('renders usage count pill on template list card', async () => {
    renderWithVolundr(<TemplatesPage />);
    await waitFor(() => expect(screen.getAllByTestId('template-card').length).toBeGreaterThan(0));
    expect(screen.getByText(/23 sessions launched/i)).toBeInTheDocument();
  });

  it('does not render usage count when usageCount is absent', async () => {
    const noUsageStore = storeWith([{ ...RICH_TEMPLATE, usageCount: undefined }]);
    renderWithVolundr(<TemplatesPage />, { templateStore: noUsageStore });
    await waitFor(() => expect(screen.getAllByTestId('template-card').length).toBeGreaterThan(0));
    expect(screen.queryByText(/sessions launched/i)).not.toBeInTheDocument();
  });

  // -----------------------------------------------------------------------
  // Detail header: Clone / Edit action buttons
  // -----------------------------------------------------------------------

  it('shows clone and edit buttons in the detail header', async () => {
    renderWithVolundr(<TemplatesPage />);
    await waitFor(() => expect(screen.getAllByTestId('template-card').length).toBeGreaterThan(0));
    expect(screen.getByRole('button', { name: /clone template/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /edit template/i })).toBeInTheDocument();
  });

  it('clone button label includes selected template name', async () => {
    renderWithVolundr(<TemplatesPage />);
    await waitFor(() => expect(screen.getAllByTestId('template-card').length).toBeGreaterThan(0));
    expect(
      screen.getByRole('button', { name: /clone template bifrost-gateway/i }),
    ).toBeInTheDocument();
  });

  it('edit button label includes selected template name', async () => {
    renderWithVolundr(<TemplatesPage />);
    await waitFor(() => expect(screen.getAllByTestId('template-card').length).toBeGreaterThan(0));
    expect(
      screen.getByRole('button', { name: /edit template bifrost-gateway/i }),
    ).toBeInTheDocument();
  });

  it('clone button label updates when template is switched', async () => {
    renderWithVolundr(<TemplatesPage />);
    await waitFor(() => expect(screen.getAllByTestId('template-card').length).toBeGreaterThan(0));
    const cards = screen.getAllByTestId('template-card');
    fireEvent.click(cards[3]!);
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: /clone template mimir-embeddings/i }),
      ).toBeInTheDocument(),
    );
  });

  // -----------------------------------------------------------------------
  // Tab content: MCP
  // -----------------------------------------------------------------------

  it('mcp tab shows empty state for template with no mcp servers', async () => {
    renderWithVolundr(<TemplatesPage />);
    await waitFor(() => expect(screen.getAllByTestId('template-card').length).toBeGreaterThan(0));
    fireEvent.click(screen.getByRole('button', { name: /niuu-platform/i }));
    fireEvent.click(screen.getByRole('tab', { name: /^mcp$/i }));
    expect(screen.getByText(/no mcp servers enabled/i)).toBeInTheDocument();
  });

  it('mcp tab shows server rows for bifrost-gateway', async () => {
    renderWithVolundr(<TemplatesPage />);
    await waitFor(() => expect(screen.getAllByTestId('template-card').length).toBeGreaterThan(0));
    fireEvent.click(screen.getByRole('tab', { name: /^mcp$/i }));
    expect(screen.getByText('filesystem')).toBeInTheDocument();
    expect(screen.getByText('git')).toBeInTheDocument();
  });

  it('mcp server card shows connection string', async () => {
    renderWithVolundr(<TemplatesPage />);
    await waitFor(() => expect(screen.getAllByTestId('template-card').length).toBeGreaterThan(0));
    fireEvent.click(screen.getByRole('tab', { name: /^mcp$/i }));
    expect(screen.getByText('uvx mcp-filesystem')).toBeInTheDocument();
  });

  it('mcp server card shows transport protocol', async () => {
    renderWithVolundr(<TemplatesPage />);
    await waitFor(() => expect(screen.getAllByTestId('template-card').length).toBeGreaterThan(0));
    fireEvent.click(screen.getByRole('tab', { name: /^mcp$/i }));
    expect(screen.getAllByText('stdio').length).toBeGreaterThanOrEqual(1);
  });

  it('mcp server card tool list is collapsed by default', async () => {
    renderWithVolundr(<TemplatesPage />);
    await waitFor(() => expect(screen.getAllByTestId('template-card').length).toBeGreaterThan(0));
    fireEvent.click(screen.getByRole('tab', { name: /^mcp$/i }));
    expect(screen.queryByText('run_script')).not.toBeInTheDocument();
  });

  it('mcp server rows render as flat rows', async () => {
    renderWithVolundr(<TemplatesPage />);
    await waitFor(() => expect(screen.getAllByTestId('template-card').length).toBeGreaterThan(0));
    fireEvent.click(screen.getByRole('tab', { name: /^mcp$/i }));
    expect(screen.getAllByTestId('mcp-server-card').length).toBe(2);
    expect(screen.queryByRole('button', { name: /^filesystem$/i })).not.toBeInTheDocument();
  });

  // -----------------------------------------------------------------------
  // Tab content: Skills
  // -----------------------------------------------------------------------

  it('skills tab shows empty state for default template', async () => {
    renderWithVolundr(<TemplatesPage />);
    await waitFor(() => expect(screen.getAllByTestId('template-card').length).toBeGreaterThan(0));
    fireEvent.click(screen.getByRole('button', { name: /niuu-platform/i }));
    fireEvent.click(screen.getByRole('tab', { name: /skills/i }));
    expect(screen.getByText(/no skills defined/i)).toBeInTheDocument();
  });

  it('skills tab shows flattened tool list for rich templates', async () => {
    renderWithVolundr(<TemplatesPage />, { templateStore: storeWith([RICH_TEMPLATE]) });
    await waitFor(() => expect(screen.getAllByTestId('template-card').length).toBeGreaterThan(0));
    fireEvent.click(screen.getByRole('tab', { name: /skills/i }));
    expect(screen.getByText(/python/)).toBeInTheDocument();
  });

  // -----------------------------------------------------------------------
  // Tab content: Rules
  // -----------------------------------------------------------------------

  it('rules tab shows empty state for default template', async () => {
    renderWithVolundr(<TemplatesPage />);
    await waitFor(() => expect(screen.getAllByTestId('template-card').length).toBeGreaterThan(0));
    fireEvent.click(screen.getByRole('button', { name: /niuu-platform/i }));
    fireEvent.click(screen.getByRole('tab', { name: /rules/i }));
    expect(screen.getByText(/no rules or constraints defined/i)).toBeInTheDocument();
  });

  it('rules tab shows cluster affinity for rich templates', async () => {
    renderWithVolundr(<TemplatesPage />, { templateStore: storeWith([RICH_TEMPLATE]) });
    await waitFor(() => expect(screen.getAllByTestId('template-card').length).toBeGreaterThan(0));
    fireEvent.click(screen.getByRole('tab', { name: /rules/i }));
    expect(screen.getByText('cl-eitri')).toBeInTheDocument();
  });

  // -----------------------------------------------------------------------
  // Tab reset on template switch
  // -----------------------------------------------------------------------

  it('resets to overview tab when switching templates', async () => {
    renderWithVolundr(<TemplatesPage />);
    await waitFor(() => expect(screen.getAllByTestId('template-card').length).toBeGreaterThan(0));
    // Navigate to runtime tab
    fireEvent.click(screen.getByRole('tab', { name: /runtime/i }));
    expect(screen.getByTestId('tab-runtime')).toBeInTheDocument();
    // Switch to a different template
    const cards = screen.getAllByTestId('template-card');
    fireEvent.click(cards[1]!);
    // Should be back on overview
    expect(screen.getByTestId('tab-overview')).toBeInTheDocument();
  });

  // -----------------------------------------------------------------------
  // CLI badge
  // -----------------------------------------------------------------------

  it('shows CLI badge in detail header', async () => {
    renderWithVolundr(<TemplatesPage />);
    await waitFor(() => expect(screen.getAllByTestId('template-card').length).toBeGreaterThan(0));
    expect(screen.getAllByTestId('cli-badge').length).toBeGreaterThan(0);
  });

  // -----------------------------------------------------------------------
  // GPU chip visibility
  // -----------------------------------------------------------------------

  it('shows GPU chip when a listed template has GPU resources', async () => {
    renderWithVolundr(<TemplatesPage />, { templateStore: storeWith([RICH_TEMPLATE]) });
    await waitFor(() => expect(screen.getAllByTestId('template-card').length).toBeGreaterThan(0));
    const cards = screen.getAllByTestId('template-card');
    const gpuCard = cards[0]!;
    expect(gpuCard.textContent).toContain('GPU');
  });

  // -----------------------------------------------------------------------
  // Rich template: mounts in overview
  // -----------------------------------------------------------------------

  it('overview tab shows mount entries when template has mounts', async () => {
    renderWithVolundr(<TemplatesPage />, { templateStore: storeWith([RICH_TEMPLATE]) });
    await waitFor(() => expect(screen.getAllByTestId('template-card').length).toBeGreaterThan(0));
    expect(screen.getByTestId('tab-overview')).toBeInTheDocument();
    expect(screen.getByText('repo-volundr')).toBeInTheDocument();
    expect(screen.getByText('data-pvc')).toBeInTheDocument();
  });

  // -----------------------------------------------------------------------
  // Rich template: workspace tab with mounts
  // -----------------------------------------------------------------------

  it('workspace tab renders mount rows with descriptions for templates with mounts', async () => {
    renderWithVolundr(<TemplatesPage />, { templateStore: storeWith([RICH_TEMPLATE]) });
    await waitFor(() => expect(screen.getAllByTestId('template-card').length).toBeGreaterThan(0));
    fireEvent.click(screen.getByRole('tab', { name: /workspace/i }));
    expect(screen.getByText('repo-volundr')).toBeInTheDocument();
    expect(screen.getByText(/shallow clone/)).toBeInTheDocument();
    expect(screen.getByText('data-pvc')).toBeInTheDocument();
    expect(screen.getByText(/persistent/)).toBeInTheDocument();
    expect(screen.getByText('read-write')).toBeInTheDocument();
    expect(screen.getByText('read-only')).toBeInTheDocument();
  });

  // -----------------------------------------------------------------------
  // Rich template: runtime tab with GPU + env vars
  // -----------------------------------------------------------------------

  it('runtime tab keeps image and lifecycle focus for gpu templates', async () => {
    renderWithVolundr(<TemplatesPage />, { templateStore: storeWith([RICH_TEMPLATE]) });
    await waitFor(() => expect(screen.getAllByTestId('template-card').length).toBeGreaterThan(0));
    fireEvent.click(screen.getByRole('tab', { name: /runtime/i }));
    expect(screen.getByText('Image')).toBeInTheDocument();
    expect(screen.getByText('Lifecycle')).toBeInTheDocument();
  });

  it('runtime tab no longer shows env vars inline', async () => {
    renderWithVolundr(<TemplatesPage />, { templateStore: storeWith([RICH_TEMPLATE]) });
    await waitFor(() => expect(screen.getAllByTestId('template-card').length).toBeGreaterThan(0));
    fireEvent.click(screen.getByRole('tab', { name: /runtime/i }));
    expect(screen.queryByText('API_URL')).not.toBeInTheDocument();
    expect(screen.queryByText('DB_PASSWORD')).not.toBeInTheDocument();
  });

  // -----------------------------------------------------------------------
  // Rich template: rules tab with tolerations
  // -----------------------------------------------------------------------

  it('rules tab shows tolerations for template with tolerations', async () => {
    renderWithVolundr(<TemplatesPage />, { templateStore: storeWith([RICH_TEMPLATE]) });
    await waitFor(() => expect(screen.getAllByTestId('template-card').length).toBeGreaterThan(0));
    fireEvent.click(screen.getByRole('tab', { name: /rules/i }));
    expect(screen.getByText('gpu-only')).toBeInTheDocument();
    expect(screen.getByText('spot')).toBeInTheDocument();
  });
});
