import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { ApiClientError } from '@niuulabs/query';
import { describe, expect, it, vi } from 'vitest';
import type { Saga } from '../domain/saga';
import { BoardWorkflowsCard } from './BoardWorkflowsCard';

const PLANNING = { id: 'workflow-planning', name: 'Ticket Planning Flow' };
const DELIVERY = { id: 'workflow-delivery', name: 'Reviewed Delivery Flow' };

const WORKFLOWS = [
  { ...PLANNING, scope: 'system', graph: { board: { version: 1 } } },
  { ...DELIVERY, scope: 'system', graph: { board: { version: 1 } } },
  { id: 'workflow-plain', name: 'No columns', scope: 'system', graph: {} },
  { id: 'workflow-mine', name: 'Mine', scope: 'user', graph: { board: { version: 1 } } },
];

function saga(boardWorkflows: Saga['boardWorkflows']): Saga {
  return { id: 'saga-1', name: 'Platform', boardWorkflows } as Saga;
}

function renderCard(current: Saga, setBoardWorkflows = vi.fn(async () => [])) {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ServicesProvider
        services={{
          ting: { setBoardWorkflows },
          'ting.workflows': { listWorkflows: async () => WORKFLOWS },
        }}
      >
        <BoardWorkflowsCard saga={current} />
      </ServicesProvider>
    </QueryClientProvider>,
  );
  return setBoardWorkflows;
}

describe('BoardWorkflowsCard', () => {
  it('says what the board runs when nothing is chosen', () => {
    renderCard(saga([]));

    expect(screen.getByText(/The board runs the project workflow/)).toBeInTheDocument();
  });

  it('lists the chosen workflows in order', () => {
    renderCard(saga([PLANNING, DELIVERY]));

    const items = within(screen.getByRole('region', { name: 'Board workflows' })).getAllByRole(
      'listitem',
    );
    expect(items.map((item) => item.textContent)).toEqual([PLANNING.name, DELIVERY.name]);
  });

  it('offers only system workflows that declare columns', async () => {
    renderCard(saga([PLANNING]));

    fireEvent.click(screen.getByRole('button', { name: 'Choose' }));

    expect(await screen.findByRole('button', { name: `Add ${DELIVERY.name}` })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: `Add ${PLANNING.name}` })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Add No columns' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Add Mine' })).toBeNull();
  });

  it('adds, orders and saves the workflows', async () => {
    const setBoardWorkflows = renderCard(saga([DELIVERY]));

    fireEvent.click(screen.getByRole('button', { name: 'Choose' }));
    fireEvent.click(await screen.findByRole('button', { name: `Add ${PLANNING.name}` }));
    fireEvent.click(screen.getByRole('button', { name: `Move ${PLANNING.name} up` }));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() =>
      expect(setBoardWorkflows).toHaveBeenCalledWith('saga-1', [PLANNING.id, DELIVERY.id]),
    );
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Save' })).toBeNull());
  });

  it('moves a workflow down and removes one', async () => {
    const setBoardWorkflows = renderCard(saga([PLANNING, DELIVERY]));

    fireEvent.click(screen.getByRole('button', { name: 'Choose' }));
    fireEvent.click(screen.getByRole('button', { name: `Move ${PLANNING.name} down` }));
    fireEvent.click(screen.getByRole('button', { name: `Remove ${PLANNING.name}` }));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(setBoardWorkflows).toHaveBeenCalledWith('saga-1', [DELIVERY.id]));
  });

  it('keeps the dialog open and says why a save was refused', async () => {
    const setBoardWorkflows = vi.fn(async () => {
      throw new ApiClientError('API request failed: 409', 409, 'Tickets are still running it');
    });
    renderCard(saga([PLANNING]), setBoardWorkflows);

    fireEvent.click(screen.getByRole('button', { name: 'Choose' }));
    fireEvent.click(screen.getByRole('button', { name: `Remove ${PLANNING.name}` }));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('still running');
    expect(screen.getByRole('button', { name: 'Save' })).toBeInTheDocument();
  });
});
