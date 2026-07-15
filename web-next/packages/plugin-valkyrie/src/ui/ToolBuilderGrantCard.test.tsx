import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import {
  createMockRealmGovernanceService,
  createSeedRealms,
  createSeedToolWorkflows,
  createSeedTrustGrants,
} from '../adapters/mock';
import type { RealmSummary } from '../domain';
import { wrapWithValkyrie } from '../testing/wrapWithValkyrie';
import { ToolBuilderGrantCard } from './ToolBuilderGrantCard';

function realmBySlug(slug: string): RealmSummary {
  const realm = createSeedRealms().find((entry) => entry.slug === slug);
  if (!realm) throw new Error(`Seed realm missing: ${slug}`);
  return realm;
}

describe('ToolBuilderGrantCard', () => {
  it('shows the loading state first', () => {
    render(<ToolBuilderGrantCard realm={realmBySlug('valhalla')} />, {
      wrapper: wrapWithValkyrie(),
    });
    expect(screen.getByTestId('tool-builder-loading')).toBeInTheDocument();
  });

  it('renders the current grant with workflow, level, and autonomy badge', async () => {
    render(<ToolBuilderGrantCard realm={realmBySlug('valhalla')} />, {
      wrapper: wrapWithValkyrie(),
    });

    const card = await screen.findByTestId('tool-builder-card');
    expect(within(card).getByTestId('tool-builder-autonomy')).toHaveTextContent(
      'autonomous · level 2',
    );
    expect(card).toHaveTextContent('valkyrie-tool-forge');
    expect(within(card).getByLabelText('Tool-builder workflow for Valhalla')).toHaveValue(
      'valkyrie-tool-forge',
    );
    expect(within(card).getByLabelText('Build trust level for Valhalla')).toHaveValue('2');
  });

  it('offers only tool-builder tagged workflows until the operator shows all', async () => {
    const user = userEvent.setup();
    render(<ToolBuilderGrantCard realm={realmBySlug('valhalla')} />, {
      wrapper: wrapWithValkyrie(),
    });

    const select = await screen.findByLabelText('Tool-builder workflow for Valhalla');
    const builderNames = createSeedToolWorkflows()
      .filter((workflow) => workflow.tags.includes('tool-builder'))
      .map((workflow) => workflow.name);
    const optionValues = () =>
      within(select)
        .getAllByRole('option')
        .map((option) => (option as HTMLOptionElement).value)
        .filter(Boolean);

    expect(optionValues()).toEqual(builderNames);
    expect(screen.getByText('2 tagged "tool-builder"')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'show all workflows' }));
    expect(optionValues()).toContain('release-train');
    expect(screen.getByText('showing all 3 workflows')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'show tool-builder only' }));
    expect(optionValues()).toEqual(builderNames);
  });

  it('saves the picked workflow and level with the exact grant body', async () => {
    const user = userEvent.setup();
    const service = createMockRealmGovernanceService();
    const createSpy = vi.spyOn(service, 'createTrustGrant');
    render(<ToolBuilderGrantCard realm={realmBySlug('valhalla')} />, {
      wrapper: wrapWithValkyrie({ 'valkyrie.realms': service }),
    });

    await user.selectOptions(
      await screen.findByLabelText('Tool-builder workflow for Valhalla'),
      'valkyrie-tool-forge-fast',
    );
    await user.selectOptions(screen.getByLabelText('Build trust level for Valhalla'), '4');
    await user.click(screen.getByTestId('tool-builder-save'));

    expect(createSpy).toHaveBeenCalledWith('valhalla', {
      action_class: 'build',
      target: '*',
      level: 4,
      limits: { workflow: 'valkyrie-tool-forge-fast' },
      granted_by: 'human:operator',
    });
    await waitFor(() =>
      expect(screen.getByTestId('tool-builder-autonomy')).toHaveTextContent('yolo · level 4'),
    );
  });

  it('maps trust levels to autonomy at the boundaries', async () => {
    const user = userEvent.setup();
    render(<ToolBuilderGrantCard realm={realmBySlug('valhalla')} />, {
      wrapper: wrapWithValkyrie(),
    });

    const level = await screen.findByLabelText('Build trust level for Valhalla');
    await user.selectOptions(level, '1');
    expect(screen.getByTestId('tool-builder-level-hint')).toHaveTextContent('guarded');
    await user.selectOptions(level, '2');
    expect(screen.getByTestId('tool-builder-level-hint')).toHaveTextContent('autonomous');
    await user.selectOptions(level, '4');
    expect(screen.getByTestId('tool-builder-level-hint')).toHaveTextContent('yolo');
  });

  it('invites the operator to grant build trust when none exists', async () => {
    render(<ToolBuilderGrantCard realm={realmBySlug('host-jozef')} />, {
      wrapper: wrapWithValkyrie(),
    });

    expect(await screen.findByTestId('tool-builder-ungranted')).toBeInTheDocument();
    expect(screen.getByTestId('tool-builder-save')).toBeDisabled();
  });

  it('surfaces load failures', async () => {
    const broken = {
      ...createMockRealmGovernanceService(),
      listTrustGrants: () => Promise.reject(new Error('grants offline')),
    };
    render(<ToolBuilderGrantCard realm={realmBySlug('valhalla')} />, {
      wrapper: wrapWithValkyrie({ 'valkyrie.realms': broken }),
    });

    expect(await screen.findByTestId('tool-builder-error')).toHaveTextContent('grants offline');
  });

  it('surfaces save failures and keeps the form editable', async () => {
    const user = userEvent.setup();
    const service = createMockRealmGovernanceService();
    service.createTrustGrant = () => Promise.reject(new Error('grant refused'));
    render(<ToolBuilderGrantCard realm={realmBySlug('valhalla')} />, {
      wrapper: wrapWithValkyrie({ 'valkyrie.realms': service }),
    });

    await user.selectOptions(await screen.findByLabelText('Build trust level for Valhalla'), '5');
    await user.click(screen.getByTestId('tool-builder-save'));

    expect(await screen.findByTestId('tool-builder-save-error')).toHaveTextContent('grant refused');
    expect(screen.getByTestId('tool-builder-save')).toBeEnabled();
  });

  it('keeps a granted workflow selectable when it lost the tag', async () => {
    const grants = createSeedTrustGrants();
    grants['valhalla'] = [
      {
        ...grants['valhalla']![0]!,
        limits: { workflow: 'release-train' },
      },
    ];
    render(<ToolBuilderGrantCard realm={realmBySlug('valhalla')} />, {
      wrapper: wrapWithValkyrie({
        'valkyrie.realms': createMockRealmGovernanceService({ grants }),
      }),
    });

    const select = await screen.findByLabelText('Tool-builder workflow for Valhalla');
    expect(select).toHaveValue('release-train');
  });
});
