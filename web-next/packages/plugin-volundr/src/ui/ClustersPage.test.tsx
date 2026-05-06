import { describe, expect, it } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ClustersPage } from './ClustersPage';
import { renderWithVolundr } from '../testing/renderWithVolundr';
import { createMockClusterAdapter } from '../adapters/mock';

describe('ClustersPage', () => {
  it('renders loading state', () => {
    renderWithVolundr(<ClustersPage />, {
      clusterAdapter: {
        ...createMockClusterAdapter(),
        getClusters: () => new Promise<never>(() => {}),
      },
    });

    expect(screen.getByText(/loading clusters/i)).toBeInTheDocument();
  });

  it('renders error state', async () => {
    renderWithVolundr(<ClustersPage />, {
      clusterAdapter: {
        ...createMockClusterAdapter(),
        getClusters: async () => {
          throw new Error('adapter offline');
        },
      },
    });

    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());
    expect(screen.getByText(/failed to load clusters/i)).toBeInTheDocument();
  });

  it('renders empty state', async () => {
    renderWithVolundr(<ClustersPage />, {
      clusterAdapter: {
        ...createMockClusterAdapter(),
        getClusters: async () => [],
      },
    });

    await waitFor(() => expect(screen.getByText(/no clusters registered/i)).toBeInTheDocument());
  });

  it('renders realm sidebar groups', async () => {
    renderWithVolundr(<ClustersPage />);

    await waitFor(() => expect(screen.getByText('ASGARD')).toBeInTheDocument());
    expect(screen.getByText('MIDGARD')).toBeInTheDocument();
    expect(screen.getByText('SVARTALFHEIM')).toBeInTheDocument();
    expect(screen.getByText('JOTUNHEIM')).toBeInTheDocument();
  });

  it('defaults to the Valaskjalf cluster presentation', async () => {
    renderWithVolundr(<ClustersPage />);

    await waitFor(() => expect(screen.getAllByText('Valaskjálf').length).toBeGreaterThan(0));
    expect(screen.getByText(/ca-hamilton-1/i)).toBeInTheDocument();
    expect(screen.getByText(/4\/4 nodes ready/i)).toBeInTheDocument();
  });

  it('renders top resource cards', async () => {
    renderWithVolundr(<ClustersPage />);

    await waitFor(() => expect(screen.getByText('CPU')).toBeInTheDocument());
    expect(screen.getByText('MEMORY')).toBeInTheDocument();
    expect(screen.getByText('GPU')).toBeInTheDocument();
    expect(screen.getByText('DISK')).toBeInTheDocument();
    expect(screen.getAllByTestId('meter')).toHaveLength(4);
  });

  it('renders pods and nodes panels', async () => {
    renderWithVolundr(<ClustersPage />);

    await waitFor(() => expect(screen.getByText(/Pods on this forge/i)).toBeInTheDocument());
    expect(screen.getByText(/^Nodes$/)).toBeInTheDocument();
    expect(screen.getByText('observatory-canvas-perf')).toBeInTheDocument();
    expect(screen.getByText('valaskjalf-01')).toBeInTheDocument();
    expect(screen.getAllByTestId('connection-type-badge').length).toBeGreaterThan(0);
  });

  it('switches selected cluster from the sidebar', async () => {
    const user = userEvent.setup();
    renderWithVolundr(<ClustersPage />);

    await waitFor(() =>
      expect(screen.getByRole('button', { name: /Valhalla/i })).toBeInTheDocument(),
    );
    await user.click(screen.getByRole('button', { name: /Valhalla/i }));

    expect(screen.getAllByText('Valhalla').length).toBeGreaterThan(0);
    expect(screen.getByText(/ca-hamilton-2/i)).toBeInTheDocument();
  });

  it('collapses and expands the sidebar', async () => {
    const user = userEvent.setup();
    renderWithVolundr(<ClustersPage />);

    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: /collapse clusters sidebar/i }),
      ).toBeInTheDocument(),
    );

    await user.click(screen.getByRole('button', { name: /collapse clusters sidebar/i }));
    expect(screen.getByRole('button', { name: /expand clusters sidebar/i })).toBeInTheDocument();
  });
});
