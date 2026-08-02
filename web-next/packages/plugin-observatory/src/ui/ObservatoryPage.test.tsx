import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { ObservatoryPage } from './ObservatoryPage';
import {
  createMockTopologyStream,
  createMockEventStream,
  createMockRegistryRepository,
  createMockAgentDirectory,
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
          'observatory.agents': createMockAgentDirectory(),
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

  it('clicking a node shows it in the inspector', () => {
    wrap(<ObservatoryPage />);

    fireEvent.click(screen.getByTestId('node-btn-realm-asgard'));

    expect(screen.getByTestId('inspector')).toHaveTextContent(/asgard/i);
  });

  it('shows a prompt in the inspector until something is selected', () => {
    // The inspector is a column now, not a dialog: it has no close button and
    // is always present, so "nothing selected" needs its own state.
    wrap(<ObservatoryPage />);

    expect(screen.getByTestId('inspector-empty')).toBeInTheDocument();
  });

  it('navigates from the inspector to a connected node', () => {
    wrap(<ObservatoryPage />);
    fireEvent.click(screen.getByTestId('node-btn-realm-asgard'));

    const peer = screen.queryAllByTestId(/^insp-peer-/)[0];
    if (peer) {
      fireEvent.click(peer);
      expect(screen.getByTestId('inspector')).toBeInTheDocument();
    }
  });

  it('renders the signal ticker rather than a floating event log', () => {
    // The mockup docks the feed beneath the stage; a floating overlay covered
    // the canvas it was describing.
    wrap(<ObservatoryPage />);
    expect(screen.getByTestId('signal-ticker')).toBeInTheDocument();
  });

  it('renders the readout and the docked inspector', () => {
    wrap(<ObservatoryPage />);
    expect(screen.getByTestId('observatory-readout')).toBeInTheDocument();
    expect(screen.getByLabelText('Inspector')).toBeInTheDocument();
  });

  it('renders the Minimap overlay with topology', () => {
    wrap(<ObservatoryPage />);
    expect(screen.getByRole('img', { name: /topology minimap/i })).toBeInTheDocument();
  });
});
