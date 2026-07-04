import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { createMockValkyrieService, createSeedValkyrieDashboard } from '../adapters/mock';
import { wrapWithValkyrie } from '../testing/wrapWithValkyrie';
import { FleetPage } from './FleetPage';

describe('FleetPage', () => {
  it('shows the loading state first', () => {
    render(<FleetPage />, { wrapper: wrapWithValkyrie() });
    expect(screen.getByTestId('fleet-loading')).toBeInTheDocument();
  });

  it('renders one card per resident with wakefulness and tools', async () => {
    const seed = createSeedValkyrieDashboard();
    render(<FleetPage />, { wrapper: wrapWithValkyrie() });

    const cards = await screen.findAllByTestId('fleet-card');
    expect(cards).toHaveLength(seed.valkyries.length);
    expect(cards[0]).toHaveTextContent(seed.valkyries[0]!.name);
    expect(cards[0]).toHaveTextContent('tools');
  });

  it('changes autonomy through the unified command path', async () => {
    const user = userEvent.setup();
    const seed = createSeedValkyrieDashboard();
    const service = createMockValkyrieService(seed);
    render(<FleetPage />, { wrapper: wrapWithValkyrie({ valkyrie: service }) });

    const target = seed.valkyries[0]!;
    const select = await screen.findByLabelText(`Autonomy mode for ${target.name}`);
    await user.selectOptions(select, 'yolo');

    await waitFor(async () => {
      const dashboard = await service.getDashboard();
      expect(dashboard.valkyries.find((entry) => entry.id === target.id)?.autonomyMode).toBe(
        'yolo',
      );
    });
  });

  it('surfaces dashboard failures', async () => {
    const broken = {
      getDashboard: () => Promise.reject(new Error('dashboard offline')),
      updateAutonomy: () => Promise.reject(new Error('nope')),
    };
    render(<FleetPage />, { wrapper: wrapWithValkyrie({ valkyrie: broken }) });
    expect(await screen.findByTestId('fleet-error')).toHaveTextContent('dashboard offline');
  });

  it('shows an empty state without residents', async () => {
    const empty = {
      getDashboard: () => Promise.resolve({ ...createSeedValkyrieDashboard(), valkyries: [] }),
      updateAutonomy: () => Promise.reject(new Error('nope')),
    };
    render(<FleetPage />, { wrapper: wrapWithValkyrie({ valkyrie: empty }) });
    expect(await screen.findByTestId('fleet-empty')).toBeInTheDocument();
  });

  it('chips the effective build governance on realms with a build grant', async () => {
    render(<FleetPage />, { wrapper: wrapWithValkyrie() });

    const cards = await screen.findAllByTestId('fleet-card');
    // Sigrun and Runa share env-k8s-valhalla → realm `valhalla`, grant level 2.
    await waitFor(() => expect(screen.getAllByTestId('fleet-governance-chip')).toHaveLength(2));
    expect(screen.getAllByTestId('fleet-governance-chip')[0]).toHaveTextContent(
      'build autonomous · L2',
    );

    // Saga (realm host-jozef) has no build grant → no chip on her card.
    const sagaCard = cards.find((card) => card.textContent?.includes('Saga'));
    expect(sagaCard).toBeDefined();
    expect(within(sagaCard!).queryByTestId('fleet-governance-chip')).toBeNull();
  });
});
