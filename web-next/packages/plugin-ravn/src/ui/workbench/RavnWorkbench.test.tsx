import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { mountRavn, ravnServices } from '../../testing/mountRavn';
import { RAVN_ID, failedRavn, makeRavn } from '../../testing/fixtures';

vi.mock('./LiveChat', () => ({
  LiveChat: ({ chatEndpoint }: { chatEndpoint: string }) => (
    <div data-testid="live-chat">{chatEndpoint}</div>
  ),
}));

beforeEach(() => localStorage.clear());

const second = '33333333-3333-4333-8333-333333333333';

describe('RavnWorkbench — list', () => {
  it('shows a loading state while the fleet is on its way', async () => {
    const services = ravnServices();
    services['ravn.ravens'].listRavens = vi.fn(() => new Promise<never>(() => undefined));
    mountRavn('/ravn', services);
    expect(await screen.findByTestId('ravn-workbench-loading')).toBeInTheDocument();
  });

  it('shows the fleet and opens the first ravn', async () => {
    mountRavn('/ravn', ravnServices());
    expect(await screen.findByTestId('ravn-list')).toBeInTheDocument();
    expect(await screen.findByRole('heading', { level: 1, name: /Muninn/ })).toBeInTheDocument();
  });

  it('says why the fleet could not be loaded and retries', async () => {
    const services = ravnServices();
    services['ravn.ravens'].listRavens = vi
      .fn()
      .mockRejectedValueOnce(
        Object.assign(new Error('API request failed: 503'), { detail: 'down' }),
      )
      .mockResolvedValue([makeRavn()]);
    mountRavn('/ravn', services);
    expect(await screen.findByText('down')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByTestId('ravn-list')).toBeInTheDocument();
  });

  it('leads with what needs attention and says why in the row', async () => {
    mountRavn(
      '/ravn',
      ravnServices({
        ravens: [makeRavn(), failedRavn({ id: second, residentName: 'Proof' })],
      }),
    );
    const list = await screen.findByTestId('ravn-list');
    expect(within(list).getByText('1 running · 1 need attention')).toBeInTheDocument();
    expect(within(list).getByText('Backend not ready · reconcile failed')).toBeInTheDocument();
    // The failed one opens by default.
    expect(await screen.findByRole('heading', { level: 1, name: /Proof/ })).toBeInTheDocument();
    expect(screen.getByTestId('ravn-health-banner')).toHaveTextContent(
      'Not running — backend not ready · reconcile failed',
    );
  });

  it('filters, searches and regroups', async () => {
    mountRavn(
      '/ravn',
      ravnServices({
        ravens: [makeRavn(), failedRavn({ id: second, residentName: 'Proof', engine: 'openclaw' })],
      }),
    );
    const list = await screen.findByTestId('ravn-list');
    fireEvent.click(screen.getByTestId('ravn-filter-attention'));
    expect(within(list).queryByText('Muninn')).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId('ravn-filter-running'));
    expect(within(list).getByText('Muninn')).toBeInTheDocument();
    expect(localStorage.getItem('ravn.workbench.filter')).toBe('"running"');

    fireEvent.click(screen.getByTestId('ravn-filter-all'));
    fireEvent.change(screen.getByTestId('ravn-search'), { target: { value: 'openclaw' } });
    expect(within(list).queryByText('Muninn')).not.toBeInTheDocument();
    fireEvent.change(screen.getByTestId('ravn-search'), { target: { value: 'zzz' } });
    expect(screen.getByTestId('ravn-list-empty')).toHaveTextContent('No ravens match');

    fireEvent.change(screen.getByTestId('ravn-search'), { target: { value: '' } });
    fireEvent.change(screen.getByTestId('ravn-grouping'), { target: { value: 'engine' } });
    expect(within(list).getByRole('region', { name: 'openclaw' })).toBeInTheDocument();
  });

  it('selecting a ravn puts it in the URL', async () => {
    const router = mountRavn(
      '/ravn',
      ravnServices({ ravens: [makeRavn(), makeRavn({ id: second, residentName: 'Regin' })] }),
    );
    fireEvent.click(await screen.findByTestId(`ravn-row-target-a:${second}`));
    await waitFor(() =>
      expect(router.state.location.search).toMatchObject({ ravn: second, instance_id: 'target-a' }),
    );
    expect(screen.getByRole('heading', { level: 1, name: /Regin/ })).toBeInTheDocument();
  });

  it('invites a first deploy when the fleet is empty', async () => {
    mountRavn('/ravn', ravnServices({ ravens: [] }));
    expect(await screen.findByTestId('ravn-workbench-empty')).toBeInTheDocument();
    expect(screen.getByTestId('ravn-list-empty')).toHaveTextContent('No ravens yet');
    fireEvent.click(screen.getByRole('button', { name: 'Deploy a ravn' }));
    expect(await screen.findByRole('dialog', { name: 'Deploy a ravn' })).toBeInTheDocument();
  });
});

describe('RavnWorkbench — ravn header', () => {
  it('shows who it is and what it runs on', async () => {
    mountRavn('/ravn', ravnServices());
    const pane = await screen.findByTestId('ravn-pane');
    expect(within(pane).getByRole('button', { name: /persona\s*reviewer/ })).toBeInTheDocument();
    expect(within(pane).getByText('claude-sonnet-4-6')).toBeInTheDocument();
    expect(within(pane).getByText('Local Forge')).toBeInTheDocument();
    expect(screen.getByTestId('ravn-talk')).toBeEnabled();
  });

  it('opens the persona from the header chip', async () => {
    const router = mountRavn('/ravn', ravnServices());
    fireEvent.click(await screen.findByRole('button', { name: /persona\s*reviewer/ }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/ravn/personas'));
    expect(router.state.location.search).toMatchObject({ persona: 'reviewer' });
  });

  it('suspends a running ravn and restarts a failed one', async () => {
    const services = ravnServices({ ravens: [makeRavn()] });
    mountRavn('/ravn', services);
    fireEvent.click(await screen.findByTestId('ravn-suspend'));
    await waitFor(() =>
      expect(services['ravn.residents'].applyLifecycle).toHaveBeenCalledWith(
        expect.objectContaining({ id: RAVN_ID }),
        'suspend',
      ),
    );
  });

  it('restarts from the health banner', async () => {
    const services = ravnServices({ ravens: [failedRavn()] });
    mountRavn('/ravn', services);
    expect(await screen.findByTestId('ravn-talk')).toBeDisabled();
    fireEvent.click(screen.getByTestId('ravn-banner-restart'));
    await waitFor(() =>
      expect(services['ravn.residents'].applyLifecycle).toHaveBeenCalledWith(
        expect.objectContaining({ id: RAVN_ID }),
        'restart',
      ),
    );
  });

  it('shows why a command failed', async () => {
    const services = ravnServices({ ravens: [failedRavn()] });
    services['ravn.residents'].applyLifecycle = vi
      .fn()
      .mockRejectedValue(Object.assign(new Error('x'), { detail: 'target unreachable' }));
    mountRavn('/ravn', services);
    fireEvent.click(await screen.findByTestId('ravn-restart'));
    expect(await screen.findByText('target unreachable')).toBeInTheDocument();
  });

  it('resumes a suspended ravn from the banner', async () => {
    const services = ravnServices({
      ravens: [makeRavn({ observedState: 'suspended', desiredState: 'suspended' })],
    });
    mountRavn('/ravn', services);
    expect(await screen.findByTestId('ravn-health-banner')).toHaveTextContent('Suspended');
    fireEvent.click(screen.getByTestId('ravn-banner-resume'));
    await waitFor(() =>
      expect(services['ravn.residents'].applyLifecycle).toHaveBeenCalledWith(
        expect.anything(),
        'resume',
      ),
    );
  });

  it('shows deployment progress while starting', async () => {
    mountRavn('/ravn', ravnServices({ ravens: [makeRavn({ observedState: 'deploying' })] }));
    const banner = await screen.findByTestId('ravn-health-banner');
    expect(banner).toHaveTextContent('Starting on Local Forge');
    expect(within(banner).getByLabelText('Deployment progress')).toHaveTextContent('Deploying');
  });

  it('shows removal in progress', async () => {
    mountRavn('/ravn', ravnServices({ ravens: [makeRavn({ observedState: 'deleting' })] }));
    expect(await screen.findByTestId('ravn-health-banner')).toHaveTextContent(
      'Removing from Local Forge',
    );
    expect(screen.queryByTestId('ravn-delete')).not.toBeInTheDocument();
  });

  it('flags a running ravn with a failing check', async () => {
    mountRavn(
      '/ravn',
      ravnServices({
        ravens: [
          makeRavn({
            conditions: [
              {
                type: 'MeshJoined',
                status: 'false',
                reason: 'PeerTimeout',
                message: 'no peers',
                lastTransitionAt: '',
              },
            ],
          }),
        ],
      }),
    );
    expect(await screen.findByTestId('ravn-health-banner')).toHaveTextContent(
      'Running with a failing check — mesh joined · peer timeout',
    );
  });

  it('says when a failed ravn reports no reason', async () => {
    mountRavn('/ravn', ravnServices({ ravens: [failedRavn({ conditions: [] })] }));
    expect(await screen.findByTestId('ravn-health-banner')).toHaveTextContent(
      'The backend reported no failing condition — the logs may say more.',
    );
  });

  it('deletes after confirmation and returns to the list', async () => {
    const services = ravnServices();
    const router = mountRavn(`/ravn?ravn=${RAVN_ID}`, services);
    fireEvent.click(await screen.findByTestId('ravn-delete'));
    const dialog = await screen.findByRole('dialog', { name: 'Delete Muninn' });
    expect(dialog).toHaveTextContent('removes the ravn runtime from Local Forge');
    fireEvent.click(within(dialog).getByTestId('ravn-delete-confirm'));
    await waitFor(() => expect(services['ravn.residents'].delete).toHaveBeenCalled());
    await waitFor(() => expect(router.state.location.search).toEqual({}));
  });

  it('keeps the dialog open with the reason when delete fails', async () => {
    const services = ravnServices();
    services['ravn.residents'].delete = vi
      .fn()
      .mockRejectedValue(Object.assign(new Error('x'), { detail: 'still reconciling' }));
    mountRavn('/ravn', services);
    fireEvent.click(await screen.findByTestId('ravn-delete'));
    fireEvent.click(await screen.findByTestId('ravn-delete-confirm'));
    expect(await screen.findByText('still reconciling')).toBeInTheDocument();
  });

  it('moves between tabs with the arrow keys', async () => {
    const router = mountRavn('/ravn', ravnServices());
    const chat = await screen.findByTestId('ravn-tab-chat');
    fireEvent.keyDown(chat, { key: 'ArrowRight' });
    await waitFor(() => expect(router.state.location.search).toMatchObject({ tab: 'activity' }));
    fireEvent.keyDown(screen.getByTestId('ravn-tab-activity'), { key: 'ArrowLeft' });
    await waitFor(() => expect(router.state.location.search).toMatchObject({ tab: 'chat' }));
    fireEvent.keyDown(screen.getByTestId('ravn-tab-chat'), { key: 'Enter' });
  });

  it('goes back to the list on narrow screens', async () => {
    const router = mountRavn(`/ravn?ravn=${RAVN_ID}`, ravnServices());
    fireEvent.click(await screen.findByRole('button', { name: 'Ravens' }));
    await waitFor(() => expect(router.state.location.search).toEqual({}));
  });
});

describe('RavnWorkbench — deploy', () => {
  it('deploys the picked runtime with a name, model and persona', async () => {
    const services = ravnServices();
    const router = mountRavn('/ravn', services);
    fireEvent.click(await screen.findByTestId('ravn-deploy-open'));
    const dialog = await screen.findByRole('dialog', { name: 'Deploy a ravn' });
    expect(await within(dialog).findByTestId('ravn-deploy-profile-nemoclaw-local')).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(within(dialog).getByTestId('ravn-deploy-submit')).toBeDisabled();
    fireEvent.change(within(dialog).getByTestId('ravn-deploy-name'), {
      target: { value: 'Ivaldi' },
    });
    fireEvent.change(within(dialog).getByLabelText('Search personas'), {
      target: { value: 'research' },
    });
    fireEvent.click(await within(dialog).findByText('research-framer'));
    expect(dialog).toHaveTextContent('Deploys Ivaldi as NemoClaw (Local) on Local Forge');
    fireEvent.click(within(dialog).getByTestId('ravn-deploy-submit'));
    await waitFor(() =>
      expect(services['ravn.residents'].deploy).toHaveBeenCalledWith({
        name: 'Ivaldi',
        profileId: 'nemoclaw-local',
        instanceId: 'target-a',
        personaName: 'research-framer',
        model: 'niuu/nvidia/nemotron-3-super',
      }),
    );
    await waitFor(() =>
      expect(router.state.location.search).toMatchObject({
        ravn: '99999999-9999-4999-8999-999999999999',
        tab: 'chat',
      }),
    );
  });

  it('shows why a deploy failed', async () => {
    const services = ravnServices();
    services['ravn.residents'].deploy = vi
      .fn()
      .mockRejectedValue(Object.assign(new Error('x'), { detail: 'name already taken' }));
    mountRavn('/ravn', services);
    fireEvent.click(await screen.findByTestId('ravn-deploy-open'));
    fireEvent.change(await screen.findByTestId('ravn-deploy-name'), {
      target: { value: 'Muninn' },
    });
    fireEvent.click(screen.getByTestId('ravn-deploy-submit'));
    expect(await screen.findByText('name already taken')).toBeInTheDocument();
  });

  it('opens with a persona picked when linked from the library', async () => {
    mountRavn('/ravn?deploy=reviewer', ravnServices());
    const dialog = await screen.findByRole('dialog', { name: 'Deploy a ravn' });
    fireEvent.change(await within(dialog).findByTestId('ravn-deploy-name'), {
      target: { value: 'Critic' },
    });
    expect(dialog).toHaveTextContent('with persona reviewer');
  });

  it('says when no profiles are enabled or they fail to load', async () => {
    const services = ravnServices();
    services['ravn.residents'].listProfiles = vi.fn().mockResolvedValue([]);
    mountRavn('/ravn', services);
    fireEvent.click(await screen.findByTestId('ravn-deploy-open'));
    expect(
      await screen.findByText('No deployment profiles are enabled on any target.'),
    ).toBeInTheDocument();
  });

  it('switches to the flock dialog', async () => {
    mountRavn('/ravn', ravnServices());
    fireEvent.click(await screen.findByTestId('ravn-deploy-open'));
    fireEvent.click(await screen.findByTestId('ravn-deploy-flock'));
    expect(await screen.findByRole('dialog', { name: 'Deploy mesh' })).toBeInTheDocument();
  });
});
