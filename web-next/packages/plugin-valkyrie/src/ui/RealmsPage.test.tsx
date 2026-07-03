import { render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { createMockRealmGovernanceService, createSeedRealms } from '../adapters/mock';
import { wrapWithValkyrie } from '../testing/wrapWithValkyrie';
import { RealmsPage } from './RealmsPage';

describe('RealmsPage', () => {
  it('shows the loading state first', () => {
    render(<RealmsPage />, { wrapper: wrapWithValkyrie() });
    expect(screen.getByTestId('realms-loading')).toBeInTheDocument();
  });

  it('renders one card per realm with its tool-builder section', async () => {
    const seed = createSeedRealms();
    render(<RealmsPage />, { wrapper: wrapWithValkyrie() });

    const cards = await screen.findAllByTestId('realm-card');
    expect(cards).toHaveLength(seed.length);
    expect(cards[0]).toHaveTextContent(seed[0]!.name);
    expect(cards[0]).toHaveTextContent(seed[0]!.autonomy_profile);
    await waitFor(() =>
      expect(screen.getAllByTestId('tool-builder-card')).toHaveLength(seed.length),
    );
  });

  it('surfaces realm list failures', async () => {
    const broken = {
      ...createMockRealmGovernanceService(),
      listRealms: () => Promise.reject(new Error('realms offline')),
    };
    render(<RealmsPage />, { wrapper: wrapWithValkyrie({ 'valkyrie.realms': broken }) });
    expect(await screen.findByTestId('realms-error')).toHaveTextContent('realms offline');
  });

  it('falls back to a generic message for non-Error failures', async () => {
    const broken = {
      ...createMockRealmGovernanceService(),
      listRealms: () => Promise.reject('boom' as unknown as Error),
    };
    render(<RealmsPage />, { wrapper: wrapWithValkyrie({ 'valkyrie.realms': broken }) });
    expect(await screen.findByTestId('realms-error')).toHaveTextContent('Unable to load realms');
  });

  it('shows an empty state without realms', async () => {
    render(<RealmsPage />, {
      wrapper: wrapWithValkyrie({
        'valkyrie.realms': createMockRealmGovernanceService({ realms: [] }),
      }),
    });
    expect(await screen.findByTestId('realms-empty')).toBeInTheDocument();
  });
});
