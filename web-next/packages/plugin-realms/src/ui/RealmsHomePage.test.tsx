import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createSeedRealms } from '@niuulabs/plugin-valkyrie';
import { createCallLog, fakeRealmService } from '../testing/fakes';
import { renderRealms } from '../testing/renderRealms';

describe('RealmsHomePage', () => {
  afterEach(() => {
    cleanup();
    localStorage.clear();
  });

  it('lists every realm as a card with its resident state', async () => {
    renderRealms('/realms');
    await screen.findByTestId('realm-card-valhalla');
    expect(screen.getByTestId('realm-cards').children.length).toBeGreaterThanOrEqual(3);
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(
      /realms, each kept by a resident/,
    );
  });

  it('shows the three realms that matter most and expands on request', async () => {
    const user = userEvent.setup();
    const log = createCallLog();
    const seeds = createSeedRealms();
    const extra = seeds.map((realm, index) => ({
      ...realm,
      id: `extra-${index}`,
      slug: `${realm.slug}-copy`,
      name: `${realm.name} copy`,
    }));
    renderRealms('/realms', { 'valkyrie.realms': fakeRealmService(log, [...seeds, ...extra]) }, log);
    const toggle = await screen.findByTestId('realm-cards-toggle');
    expect(screen.getByTestId('realm-cards').children).toHaveLength(3);
    expect(toggle).toHaveTextContent('Show all 6 realms');
    await user.click(toggle);
    expect(screen.getByTestId('realm-cards').children).toHaveLength(6);
    expect(screen.getByTestId('realm-cards-toggle')).toHaveTextContent('matter most');
  });

  it('shows the empty state when there are no realms', async () => {
    const log = createCallLog();
    renderRealms('/realms', { 'valkyrie.realms': fakeRealmService(log, []) }, log);
    await screen.findByText('No realms yet');
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Start with one sentence');
  });

  it('turns a sentence into a draft realm', async () => {
    const user = userEvent.setup();
    const { router } = renderRealms('/realms');
    await screen.findByTestId('sentence-composer');
    expect(screen.getByTestId('sentence-submit')).toBeDisabled();
    await user.type(screen.getByTestId('sentence-input'), 'Keep niuulabs/lexi-api shippable');
    await user.click(screen.getByTestId('sentence-submit'));
    await waitFor(() => expect(router.state.location.pathname).toBe('/realms/new'));
    expect(router.state.location.search).toMatchObject({
      sentence: 'Keep niuulabs/lexi-api shippable',
    });
  });

  it('lists the templates on the templates tab', async () => {
    renderRealms('/realms/templates');
    await screen.findByText('Product resident');
    expect(screen.getAllByText('Use this template')).toHaveLength(4);
  });

  it('lets you decide a pending review from the needs-you tab', async () => {
    const user = userEvent.setup();
    renderRealms('/realms/needs-you');
    const approve = (await screen.findAllByRole('button', { name: 'Approve' }))[0]!;
    await user.click(approve);
    await waitFor(() =>
      expect(screen.queryAllByRole('button', { name: 'Approve' }).length).toBeLessThan(10),
    );
  });

  it('opens the clone picker and routes to the wizard with the source realm', async () => {
    const user = userEvent.setup();
    const { router } = renderRealms('/realms');
    await screen.findByTestId('realm-card-valhalla');
    await user.click(screen.getByRole('button', { name: 'Clone a realm' }));
    const picker = await screen.findByTestId('clone-picker');
    await user.click(picker.querySelector('button')!);
    await waitFor(() => expect(router.state.location.pathname).toBe('/realms/new'));
    expect(router.state.location.search).toMatchObject({ from: 'valhalla' });
  });
});
