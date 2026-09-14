import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createCallLog, fakeVolundr } from '../testing/fakes';
import { renderRealms } from '../testing/renderRealms';

describe('NewRealmPage', () => {
  afterEach(() => {
    cleanup();
    localStorage.clear();
  });

  it('walks template → connect → charter → launch on the existing form widgets', async () => {
    const user = userEvent.setup();
    renderRealms('/realms/new');
    await screen.findByTestId('wizard-step-template');
    await user.click(screen.getByTestId('template-qa-resident'));
    await user.click(screen.getByTestId('realm-continue'));
    await screen.findByTestId('wizard-step-connect');
    await waitFor(() => expect(screen.getByTestId('realm-repo')).toBeInTheDocument());
    await user.click(screen.getByTestId('realm-continue'));
    await screen.findByTestId('wizard-step-charter');
    expect(screen.getByTestId('trust-ladder')).toHaveTextContent('observe');
    await user.type(screen.getByTestId('realm-title'), 'Lexi API');
    expect(screen.getByTestId('realm-slug')).toHaveValue('lexi-api');
    await user.click(screen.getByTestId('realm-continue'));
    await screen.findByTestId('wizard-step-launch');
    expect(screen.getByText('Realm draft')).toBeInTheDocument();
  });

  it('opens on the launch step with the inferred rows when given a sentence', async () => {
    const { router } = renderRealms(
      '/realms/new?sentence=' +
        encodeURIComponent(
          'Keep niuulabs/lexi-api shippable, work the LXA board, and ask me before anything deploys.',
        ),
    );
    await screen.findByTestId('wizard-step-launch');
    expect(router.state.location.pathname).toBe('/realms/new');
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Here is how I read that.');
    expect(screen.getAllByText('from your sentence').length).toBeGreaterThanOrEqual(2);
    await waitFor(() =>
      expect(screen.getAllByText('niuulabs/lexi-api').length).toBeGreaterThanOrEqual(1),
    );
  });

  it('runs the recipe on go and lands on the realm page', async () => {
    const user = userEvent.setup();
    const { router, log } = renderRealms(
      '/realms/new?sentence=' +
        encodeURIComponent('Keep niuulabs/lexi-api shippable, work the LXA board.'),
    );
    await screen.findByTestId('wizard-step-launch');
    // Fill what a sentence cannot: profile and memory target come from the connect/charter steps.
    await user.click(screen.getByRole('button', { name: 'Review step by step' }));
    await screen.findByTestId('wizard-step-connect');
    await waitFor(() => expect(log.calls).toContain('getDeployments'));
    await user.click(screen.getByTestId('realm-continue'));
    await screen.findByTestId('wizard-step-charter');
    await waitFor(() => expect(screen.getByTestId('realm-slug')).toHaveValue('lexi-api'));
    await user.click(screen.getByTestId('realm-continue'));
    await screen.findByTestId('wizard-step-launch');
    await user.click(screen.getByTestId('realm-go'));
    await waitFor(() => expect(router.state.location.pathname).toBe('/realms/lexi-api'), {
      timeout: 5000,
    });
    expect(log.calls).toContain('createRealm:lexi-api');
    expect(log.calls).toContain('deploy:lexi-api:realm-lexi-api');
  });

  it('shows the failing step and the Advanced link when a step fails', async () => {
    const user = userEvent.setup();
    const log = createCallLog();
    renderRealms(
      '/realms/new?sentence=' +
        encodeURIComponent('Keep niuulabs/lexi-api shippable, work the LXA board.'),
      { volundr: fakeVolundr(log, { failIntegration: 'int-1' }) },
      log,
    );
    await screen.findByTestId('wizard-step-launch');
    await user.click(screen.getByRole('button', { name: 'Review step by step' }));
    await screen.findByTestId('wizard-step-connect');
    await user.click(await screen.findByRole('checkbox'));
    await user.click(screen.getByTestId('realm-continue'));
    await screen.findByTestId('wizard-step-charter');
    await waitFor(() => expect(screen.getByTestId('realm-slug')).toHaveValue('lexi-api'));
    await user.click(screen.getByTestId('realm-continue'));
    await screen.findByTestId('wizard-step-launch');
    await user.click(screen.getByTestId('realm-go'));
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('test connections');
    expect(within(alert).getByRole('link', { name: 'Advanced mode' })).toBeInTheDocument();
    expect(screen.getByTestId('recipe-step-realm')).toHaveAttribute('data-state', 'todo');
  });
});
