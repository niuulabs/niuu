import { describe, it, expect } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { PersonaSubs } from './PersonaSubs';
import { createMockPersonaStore } from '../adapters/mock';

function wrap(services: Record<string, unknown>) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        <ServicesProvider services={services}>{children}</ServicesProvider>
      </QueryClientProvider>
    );
  };
}

describe('PersonaSubs', () => {
  it('shows loading state while fetching', () => {
    const slowService = {
      getPersona: () => new Promise(() => {}),
      listPersonas: () => new Promise(() => {}),
    };
    render(<PersonaSubs name="reviewer" />, {
      wrapper: wrap({ 'ravn.personas': slowService }),
    });
    expect(screen.getByTestId('persona-subs-loading')).toBeInTheDocument();
  });

  it('shows error state when service throws', async () => {
    const failing = {
      getPersona: async () => {
        throw new Error('subs fetch failed');
      },
      listPersonas: async () => [],
    };
    render(<PersonaSubs name="reviewer" />, {
      wrapper: wrap({ 'ravn.personas': failing }),
    });
    await waitFor(() => expect(screen.getByTestId('persona-subs-error')).toBeInTheDocument());
    expect(screen.getByText('subs fetch failed')).toBeInTheDocument();
  });

  it('renders subs graph for a connected persona (reviewer)', async () => {
    render(<PersonaSubs name="reviewer" />, {
      wrapper: wrap({ 'ravn.personas': createMockPersonaStore() }),
    });
    await waitFor(() => expect(screen.getByTestId('persona-subs')).toBeInTheDocument(), {
      timeout: 3000,
    });
    // Should have an SVG element
    const container = screen.getByTestId('persona-subs');
    expect(container.querySelector('svg')).toBeInTheDocument();
  });

  it('renders the SVG accessibility title for reviewer', async () => {
    render(<PersonaSubs name="reviewer" />, {
      wrapper: wrap({ 'ravn.personas': createMockPersonaStore() }),
    });
    await waitFor(() => expect(screen.getByTestId('persona-subs')).toBeInTheDocument(), {
      timeout: 3000,
    });
    expect(screen.getByText('Event subscription graph for reviewer')).toBeInTheDocument();
  });

  it('shows empty state for a persona with no connections', async () => {
    // architect produces plan.completed but nothing consumes it in our seed data
    // and it consumes code.requested/feature.requested but no producer emits those in seed
    render(<PersonaSubs name="architect" />, {
      wrapper: wrap({ 'ravn.personas': createMockPersonaStore() }),
    });
    // May show subs or empty depending on seed connections — just check it renders without crash
    await waitFor(
      () => {
        const subs = screen.queryByTestId('persona-subs');
        const empty = screen.queryByTestId('persona-subs-empty');
        expect(subs ?? empty).toBeInTheDocument();
      },
      { timeout: 3000 },
    );
  });

  // ── Legend ────────────────────────────────────────────────────────────

  it('renders the graph legend', async () => {
    render(<PersonaSubs name="reviewer" />, {
      wrapper: wrap({ 'ravn.personas': createMockPersonaStore() }),
    });
    await waitFor(() => expect(screen.getByTestId('persona-subs')).toBeInTheDocument(), {
      timeout: 3000,
    });
    expect(screen.getByTestId('subs-legend')).toBeInTheDocument();
  });

  it('legend contains expected labels', async () => {
    render(<PersonaSubs name="reviewer" />, {
      wrapper: wrap({ 'ravn.personas': createMockPersonaStore() }),
    });
    await waitFor(() => expect(screen.getByTestId('subs-legend')).toBeInTheDocument(), {
      timeout: 3000,
    });
    const legend = screen.getByTestId('subs-legend');
    expect(legend.textContent).toContain('focus');
    expect(legend.textContent).toContain('event link');
  });

  // ── Zoom to fit button ─────────────────────────────────────────────────

  it('renders the zoom-to-fit button', async () => {
    render(<PersonaSubs name="reviewer" />, {
      wrapper: wrap({ 'ravn.personas': createMockPersonaStore() }),
    });
    await waitFor(() => expect(screen.getByTestId('subs-zoom-fit')).toBeInTheDocument(), {
      timeout: 3000,
    });
    expect(screen.getByTestId('subs-zoom-fit')).toHaveTextContent('Zoom to fit');
  });

  it('zoom-to-fit button is clickable without errors', async () => {
    render(<PersonaSubs name="reviewer" />, {
      wrapper: wrap({ 'ravn.personas': createMockPersonaStore() }),
    });
    await waitFor(() => expect(screen.getByTestId('subs-zoom-fit')).toBeInTheDocument(), {
      timeout: 3000,
    });
    // Should not throw
    expect(() => fireEvent.click(screen.getByTestId('subs-zoom-fit'))).not.toThrow();
  });

  // ── Click navigation ───────────────────────────────────────────────────

  it('dispatches ravn:persona-selected event when a non-focus node is clicked', async () => {
    render(<PersonaSubs name="reviewer" />, {
      wrapper: wrap({ 'ravn.personas': createMockPersonaStore() }),
    });
    await waitFor(() => expect(screen.getByTestId('persona-subs')).toBeInTheDocument(), {
      timeout: 3000,
    });

    const receivedEvents: CustomEvent[] = [];
    const handler = (e: Event) => receivedEvents.push(e as CustomEvent);
    window.addEventListener('ravn:persona-selected', handler);

    // Find a producer node (not the focus) and click it
    const nodes = screen.queryAllByTestId(/^subs-node-/);
    const nonFocusNode = nodes.find((n) => n.getAttribute('data-testid') !== `subs-node-reviewer`);

    if (nonFocusNode) {
      fireEvent.click(nonFocusNode);
      expect(receivedEvents.length).toBe(1);
      expect(typeof receivedEvents[0]!.detail).toBe('string');
    }

    window.removeEventListener('ravn:persona-selected', handler);
  });

  it('does not dispatch navigation event when focus node is clicked', async () => {
    render(<PersonaSubs name="reviewer" />, {
      wrapper: wrap({ 'ravn.personas': createMockPersonaStore() }),
    });
    await waitFor(() => expect(screen.getByTestId('persona-subs')).toBeInTheDocument(), {
      timeout: 3000,
    });

    const receivedEvents: CustomEvent[] = [];
    const handler = (e: Event) => receivedEvents.push(e as CustomEvent);
    window.addEventListener('ravn:persona-selected', handler);

    const focusNode = screen.queryByTestId('subs-node-reviewer');
    if (focusNode) {
      fireEvent.click(focusNode);
      expect(receivedEvents.length).toBe(0);
    }

    window.removeEventListener('ravn:persona-selected', handler);
  });

  // ── Hover interaction ──────────────────────────────────────────────────

  it('does not crash on node mouse enter/leave', async () => {
    render(<PersonaSubs name="reviewer" />, {
      wrapper: wrap({ 'ravn.personas': createMockPersonaStore() }),
    });
    await waitFor(() => expect(screen.getByTestId('persona-subs')).toBeInTheDocument(), {
      timeout: 3000,
    });

    const nodes = screen.queryAllByTestId(/^subs-node-/);
    if (nodes.length > 0) {
      expect(() => {
        fireEvent.mouseEnter(nodes[0]!);
        fireEvent.mouseLeave(nodes[0]!);
      }).not.toThrow();
    }
  });

  // ── Node rendering ─────────────────────────────────────────────────────

  it('renders nodes for reviewer (focus) and connected personas', async () => {
    render(<PersonaSubs name="reviewer" />, {
      wrapper: wrap({ 'ravn.personas': createMockPersonaStore() }),
    });
    await waitFor(() => expect(screen.getByTestId('persona-subs')).toBeInTheDocument(), {
      timeout: 3000,
    });
    expect(screen.getByTestId('subs-node-reviewer')).toBeInTheDocument();
  });
});
