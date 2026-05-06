import { describe, it, expect } from 'vitest';
import { screen, waitFor, fireEvent } from '@testing-library/react';
import { RavnsPage } from './RavnsPage';
import { createMimirMockAdapter } from '../adapters/mock';
import type { IMimirService } from '../ports';
import { renderWithMimir } from '../testing/renderWithMimir';

const wrap = renderWithMimir;

describe('RavnsPage', () => {
  it('renders the page title', () => {
    wrap(<RavnsPage />);
    expect(screen.getByRole('heading', { name: /ravns/i })).toBeInTheDocument();
  });

  it('shows loading state initially', () => {
    wrap(<RavnsPage />);
    expect(screen.getByText(/loading ravns/)).toBeInTheDocument();
  });

  it('renders ravn items after load', async () => {
    wrap(<RavnsPage />);
    await waitFor(() => expect(screen.getAllByTestId('ravn-item').length).toBeGreaterThan(0));
  });

  it('shows each ravn id', async () => {
    wrap(<RavnsPage />);
    await waitFor(() => expect(screen.getByText('ravn-fjolnir')).toBeInTheDocument());
    expect(screen.getByText('ravn-skald')).toBeInTheDocument();
  });

  it('shows ravn state labels', async () => {
    wrap(<RavnsPage />);
    await waitFor(() => expect(screen.getAllByTestId('ravn-state').length).toBeGreaterThan(0));
    const states = screen.getAllByTestId('ravn-state');
    const texts = states.map((el) => el.textContent);
    expect(texts).toContain('active');
    expect(texts).toContain('idle');
  });

  it('shows bio text for each ravn card', async () => {
    wrap(<RavnsPage />);
    await waitFor(() => expect(screen.getAllByTestId('ravn-bio').length).toBeGreaterThan(0));
    const bios = screen.getAllByTestId('ravn-bio');
    expect(bios[0]!.textContent).toBeTruthy();
    expect(bios[0]!.textContent).not.toBe('');
  });

  it('shows pages-touched count in metrics row', async () => {
    wrap(<RavnsPage />);
    await waitFor(() => expect(screen.getAllByTestId('ravn-item').length).toBeGreaterThan(0));
    expect(screen.getAllByText(/pages touched/).length).toBeGreaterThan(0);
  });

  it('shows last dream timestamp in metrics row for ravns with dream cycles', async () => {
    wrap(<RavnsPage />);
    await waitFor(() => expect(screen.getAllByTestId('ravn-dream').length).toBeGreaterThan(0));
    const dreamEl = screen.getAllByTestId('ravn-dream')[0]!;
    expect(dreamEl.textContent).toMatch(/last dream/i);
  });

  it('shows "no dream cycles yet" for ravns without dreams', async () => {
    wrap(<RavnsPage />);
    // ravn-galdra and ravn-vor both have lastDream: null
    await waitFor(() => expect(screen.getAllByTestId('ravn-no-dream').length).toBeGreaterThan(0));
  });

  it('shows mount chips for each ravn', async () => {
    wrap(<RavnsPage />);
    await waitFor(() => expect(screen.getAllByTestId('ravn-item').length).toBeGreaterThan(0));
    expect(screen.getAllByText(/local/).length).toBeGreaterThan(0);
  });

  it('clicking a ravn card shows the profile view', async () => {
    wrap(<RavnsPage />);
    await waitFor(() => expect(screen.getAllByTestId('ravn-item').length).toBeGreaterThan(0));
    fireEvent.click(screen.getAllByTestId('ravn-item')[0]!);
    await waitFor(() => expect(screen.getByTestId('ravn-profile')).toBeInTheDocument());
  });

  it('profile view shows a back button that returns to directory', async () => {
    wrap(<RavnsPage />);
    await waitFor(() => expect(screen.getAllByTestId('ravn-item').length).toBeGreaterThan(0));
    fireEvent.click(screen.getAllByTestId('ravn-item')[0]!);
    await waitFor(() => screen.getByTestId('ravn-profile'));
    fireEvent.click(screen.getByRole('button', { name: /back to wardens/i }));
    await waitFor(() => expect(screen.getAllByTestId('ravn-item').length).toBeGreaterThan(0));
  });

  it('profile view shows dream stats for selected ravn', async () => {
    wrap(<RavnsPage />);
    await waitFor(() => expect(screen.getAllByTestId('ravn-item').length).toBeGreaterThan(0));
    // Find a ravn with dream stats (first one from mock has a dream cycle)
    fireEvent.click(screen.getAllByTestId('ravn-item')[0]!);
    await waitFor(() => screen.getByTestId('ravn-profile'));
    expect(screen.getByTestId('ravn-dream')).toBeInTheDocument();
  });

  it('profile view shows expertise section', async () => {
    wrap(<RavnsPage />);
    await waitFor(() => expect(screen.getAllByTestId('ravn-item').length).toBeGreaterThan(0));
    fireEvent.click(screen.getAllByTestId('ravn-item')[0]!);
    await waitFor(() => screen.getByTestId('ravn-profile'));
    expect(screen.getByTestId('ravn-expertise')).toBeInTheDocument();
    expect(screen.getByText(/areas of expertise/i)).toBeInTheDocument();
  });

  it('profile view shows expertise chips from ravn data', async () => {
    wrap(<RavnsPage />);
    await waitFor(() => expect(screen.getAllByTestId('ravn-item').length).toBeGreaterThan(0));
    fireEvent.click(screen.getAllByTestId('ravn-item')[0]!);
    await waitFor(() => screen.getByTestId('ravn-profile'));
    // ravn-fjolnir has expertise: ['infra', 'api', 'arch']
    expect(screen.getByText('infra')).toBeInTheDocument();
    expect(screen.getByText('api')).toBeInTheDocument();
    expect(screen.getByText('arch')).toBeInTheDocument();
  });

  it('profile view shows tools list in hero section', async () => {
    wrap(<RavnsPage />);
    await waitFor(() => expect(screen.getAllByTestId('ravn-item').length).toBeGreaterThan(0));
    fireEvent.click(screen.getAllByTestId('ravn-item')[0]!);
    await waitFor(() => screen.getByTestId('ravn-profile'));
    expect(screen.getByTestId('ravn-tools')).toBeInTheDocument();
    // ravn-fjolnir has tools: ['mimir', 'web', 'file', 'ravn']
    expect(screen.getByText(/tools:.*mimir/i)).toBeInTheDocument();
  });

  it('keyboard Enter on a ravn card opens the profile', async () => {
    wrap(<RavnsPage />);
    await waitFor(() => expect(screen.getAllByTestId('ravn-item').length).toBeGreaterThan(0));
    const card = screen.getAllByTestId('ravn-item')[0]!;
    fireEvent.keyDown(card, { key: 'Enter' });
    await waitFor(() => expect(screen.getByTestId('ravn-profile')).toBeInTheDocument());
  });

  it('profile view shows no-dream message for ravn with no dream cycles', async () => {
    wrap(<RavnsPage />);
    await waitFor(() => expect(screen.getAllByTestId('ravn-item').length).toBeGreaterThan(0));
    // ravn-galdra is the 3rd card and has no dream cycles
    fireEvent.click(screen.getAllByTestId('ravn-item')[2]!);
    await waitFor(() => screen.getByTestId('ravn-profile'));
    expect(screen.getByTestId('ravn-no-dream')).toBeInTheDocument();
  });

  it('profile view shows "no expertise defined" for ravn with empty expertise', async () => {
    const noExpertise: IMimirService = {
      ...createMimirMockAdapter(),
      mounts: {
        ...createMimirMockAdapter().mounts,
        listRavnBindings: async () => [
          {
            ravnId: 'ravn-test',
            ravnRune: 'ᚱ',
            role: 'index',
            state: 'idle',
            mountNames: ['local'],
            writeMount: 'local',
            lastDream: null,
            bio: 'Test ravn with no expertise',
            pagesTouched: 0,
            expertise: [],
            tools: ['mimir'],
          },
        ],
      },
    };
    wrap(<RavnsPage />, noExpertise);
    await waitFor(() => expect(screen.getAllByTestId('ravn-item').length).toBeGreaterThan(0));
    fireEvent.click(screen.getAllByTestId('ravn-item')[0]!);
    await waitFor(() => screen.getByTestId('ravn-profile'));
    expect(screen.getByText(/no expertise defined/i)).toBeInTheDocument();
  });

  it('shows error state when service throws', async () => {
    const failing: IMimirService = {
      ...createMimirMockAdapter(),
      mounts: {
        ...createMimirMockAdapter().mounts,
        listRavnBindings: async () => {
          throw new Error('ravns service down');
        },
      },
    };
    wrap(<RavnsPage />, failing);
    await waitFor(() => expect(screen.getByText('ravns service down')).toBeInTheDocument());
  });

  it('shows empty message when bindings list is empty', async () => {
    const empty: IMimirService = {
      ...createMimirMockAdapter(),
      mounts: {
        ...createMimirMockAdapter().mounts,
        listRavnBindings: async () => [],
      },
    };
    wrap(<RavnsPage />, empty);
    await waitFor(() => expect(screen.getByText(/no ravn bindings/i)).toBeInTheDocument());
  });
});
