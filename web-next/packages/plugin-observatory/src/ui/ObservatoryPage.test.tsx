import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { ObservatoryPage } from './ObservatoryPage';
import {
  createMockTopologyStream,
  createMockEventStream,
  createMockRegistryRepository,
} from '../adapters/mock';
import { makeCtxMock } from './TopologyCanvas/test-helpers';
import { __resetObservatoryStore } from '../application/useObservatoryStore';

beforeEach(() => {
  // Reset the module-level singleton to prevent state leaking between tests.
  __resetObservatoryStore();
  HTMLCanvasElement.prototype.getContext = vi.fn().mockReturnValue(makeCtxMock());
  vi.stubGlobal('requestAnimationFrame', vi.fn().mockReturnValue(0));
  vi.stubGlobal('cancelAnimationFrame', vi.fn());
  vi.stubGlobal('devicePixelRatio', 1);
});

function wrap(ui: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ServicesProvider
        services={{
          'observatory.topology': createMockTopologyStream(),
          'observatory.events': createMockEventStream(),
          'observatory.registry': createMockRegistryRepository(),
        }}
      >
        {ui}
      </ServicesProvider>
    </QueryClientProvider>,
  );
}

describe('ObservatoryPage', () => {
  it('renders the observatory page wrapper', () => {
    wrap(<ObservatoryPage />);
    expect(screen.getByTestId('observatory-page')).toBeInTheDocument();
  });

  it('renders the topology canvas', () => {
    wrap(<ObservatoryPage />);
    expect(screen.getByTestId('topology-canvas')).toBeInTheDocument();
  });

  it('renders camera controls', () => {
    wrap(<ObservatoryPage />);
    expect(screen.getByTestId('camera-controls')).toBeInTheDocument();
  });

  it('renders the minimap panel', () => {
    wrap(<ObservatoryPage />);
    expect(screen.getByTestId('minimap-panel')).toBeInTheDocument();
  });

  it('renders zoom controls', () => {
    wrap(<ObservatoryPage />);
    expect(screen.getByRole('button', { name: /zoom in/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /zoom out/i })).toBeInTheDocument();
  });

  it('renders camera reset button', () => {
    wrap(<ObservatoryPage />);
    expect(screen.getByTestId('camera-reset')).toBeInTheDocument();
  });

  it('renders without crash when topology stream has no data yet', () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const nullStream = {
      getSnapshot: () => null,
      subscribe: (listener: (t: never) => void) => {
        void listener;
        return () => {};
      },
    };
    expect(() =>
      render(
        <QueryClientProvider client={client}>
          <ServicesProvider
            services={{
              'observatory.topology': nullStream,
              'observatory.events': createMockEventStream(),
              'observatory.registry': createMockRegistryRepository(),
            }}
          >
            <ObservatoryPage />
          </ServicesProvider>
        </QueryClientProvider>,
      ),
    ).not.toThrow();
  });

  it('renders topology node list', () => {
    wrap(<ObservatoryPage />);
    expect(screen.getByTestId('topology-node-list')).toBeInTheDocument();
    // Seed topology includes asgard realm node
    expect(screen.getByTestId('node-btn-realm-asgard')).toBeInTheDocument();
  });

  it('clicking a node opens the EntityDrawer', () => {
    wrap(<ObservatoryPage />);
    const realmBtn = screen.getByTestId('node-btn-realm-asgard');
    fireEvent.click(realmBtn);
    // Drawer should be open — title "asgard" appears in the dialog
    expect(screen.getByRole('dialog', { name: /asgard/i })).toBeInTheDocument();
  });

  it('drawer closes when the close button is clicked', () => {
    wrap(<ObservatoryPage />);
    fireEvent.click(screen.getByTestId('node-btn-realm-asgard'));
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /close/i }));
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('clicking a resident in the drawer navigates to that node', () => {
    wrap(<ObservatoryPage />);
    // Open realm drawer — realm contains clusters and host
    fireEvent.click(screen.getByTestId('node-btn-realm-asgard'));
    expect(screen.getByRole('dialog', { name: /asgard/i })).toBeInTheDocument();
    // Click a resident (cluster-valaskjalf)
    const residentBtn = screen.getByTestId('resident-cluster-valaskjalf');
    fireEvent.click(residentBtn);
    // Drawer should now show the cluster node
    expect(screen.getByRole('dialog', { name: /valask/i })).toBeInTheDocument();
  });

  it('renders the EventLog overlay', () => {
    wrap(<ObservatoryPage />);
    expect(screen.getByTestId('event-log')).toBeInTheDocument();
  });

  it('renders the Minimap overlay with topology', () => {
    wrap(<ObservatoryPage />);
    expect(screen.getByRole('img', { name: /topology minimap/i })).toBeInTheDocument();
  });
});
