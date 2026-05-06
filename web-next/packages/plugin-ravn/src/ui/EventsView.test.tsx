import { describe, it, expect } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { EventsView } from './EventsView';
import { createMockPersonaStore } from '../adapters/mock';
import { wrapWithServices } from '../testing/wrapWithRavn';

const wrap = wrapWithServices;

const services = { 'ravn.personas': createMockPersonaStore() };

describe('EventsView', () => {
  it('shows loading state initially', () => {
    render(<EventsView />, { wrapper: wrap(services) });
    expect(screen.getByText(/loading event catalog/i)).toBeInTheDocument();
  });

  it('renders event cards after loading', async () => {
    render(<EventsView />, { wrapper: wrap(services) });
    await waitFor(() => expect(screen.getByLabelText(/event graph/i)).toBeInTheDocument());
  });

  it('shows event count', async () => {
    render(<EventsView />, { wrapper: wrap(services) });
    await waitFor(() => expect(screen.getByText(/events/i)).toBeInTheDocument());
  });

  it('renders code.changed event', async () => {
    render(<EventsView />, { wrapper: wrap(services) });
    await waitFor(() => expect(screen.getByLabelText('event code.changed')).toBeInTheDocument());
  });

  it('shows produces/consumes edge labels', async () => {
    render(<EventsView />, { wrapper: wrap(services) });
    await waitFor(() => expect(screen.getAllByText('produces').length).toBeGreaterThan(0));
    await waitFor(() => expect(screen.getAllByText('consumes').length).toBeGreaterThan(0));
  });

  it('clicking a persona pill highlights it', async () => {
    render(<EventsView />, { wrapper: wrap(services) });
    await waitFor(() => expect(screen.getByLabelText('event code.changed')).toBeInTheDocument());
    const coderBtn = screen.getAllByRole('button', { name: 'coder' })[0]!;
    fireEvent.click(coderBtn);
    expect(coderBtn).toHaveAttribute('aria-pressed', 'true');
  });

  it('shows persona filter indicator when persona is selected', async () => {
    render(<EventsView />, { wrapper: wrap(services) });
    await waitFor(() => expect(screen.getByLabelText('event code.changed')).toBeInTheDocument());
    const coderBtn = screen.getAllByRole('button', { name: 'coder' })[0]!;
    fireEvent.click(coderBtn);
    expect(screen.getByText(/filtering by/i)).toBeInTheDocument();
  });

  it('clear button removes persona filter', async () => {
    render(<EventsView />, { wrapper: wrap(services) });
    await waitFor(() => expect(screen.getByLabelText('event code.changed')).toBeInTheDocument());
    const coderBtn = screen.getAllByRole('button', { name: 'coder' })[0]!;
    fireEvent.click(coderBtn);
    const clearBtn = screen.getByLabelText(/clear persona filter/i);
    fireEvent.click(clearBtn);
    expect(screen.queryByText(/filtering by/i)).not.toBeInTheDocument();
  });

  it('shows error state when service fails', async () => {
    const failing = {
      listPersonas: async () => {
        throw new Error('fetch failed');
      },
    };
    render(<EventsView />, { wrapper: wrap({ 'ravn.personas': failing }) });
    await waitFor(() => expect(screen.getByText(/failed to load personas/i)).toBeInTheDocument());
  });
});
