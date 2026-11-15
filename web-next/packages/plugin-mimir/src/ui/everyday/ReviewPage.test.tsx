import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderEveryday } from '../../testing/renderEveryday';
import { createMemoryGatewayMockAdapter } from '../../adapters/memoryGateway';
import type { IMemoryGatewayService } from '../../ports/IMemoryGateway';
import type { MemoryProposal } from '../../domain/memoryGateway';
import { ReviewPage } from './ReviewPage';

function gateway(overrides: Partial<IMemoryGatewayService> = {}): IMemoryGatewayService {
  return { ...createMemoryGatewayMockAdapter(), ...overrides };
}

const PERSONS: MemoryProposal = {
  id: 'p-9',
  layer: 'org',
  layerName: 'the org',
  title: 'On-call',
  now: null,
  would: 'On-call is a weekly rota.',
  why: null,
  by: 'person',
  createdAt: '2026-11-11T09:00:00Z',
  sources: [],
};

describe('ReviewPage', () => {
  afterEach(cleanup);

  it('shows one change at a time: what it says now, what it would say, and why', async () => {
    renderEveryday(ReviewPage, '/mimir/review');
    const card = await screen.findByRole('article', { name: 'The booking cut-over date' });
    expect(screen.getByText('Changes to what Booking platform knows')).toBeInTheDocument();
    expect(screen.getByText('1 of 2')).toBeInTheDocument();
    expect(card).toHaveTextContent('It says nowThe cut-over is the weekend of 5 December.');
    expect(card).toHaveTextContent('It would sayThe cut-over is the weekend of 12 December.');
    expect(card).toHaveTextContent('The payments freeze moved a week.');
    expect(screen.getByRole('link', { name: 'LIN-501' })).toHaveAttribute(
      'href',
      'https://linear.app/acme/issue/LIN-501',
    );
    expect(card).toHaveTextContent('An agent picked it up from LIN-501.');
  });

  it('keeps one, then discards the next, counting as it goes', async () => {
    const user = userEvent.setup();
    const service = gateway();
    const keep = vi.spyOn(service, 'keep');
    const discard = vi.spyOn(service, 'discard');
    renderEveryday(ReviewPage, '/mimir/review', service);
    await screen.findByRole('article', { name: 'The booking cut-over date' });
    await user.click(screen.getByRole('button', { name: 'Keep' }));
    const next = await screen.findByRole('article', { name: 'Who decides the cut-over' });
    expect(keep).toHaveBeenCalledWith(expect.objectContaining({ id: 'p-1' }));
    expect(screen.getByText('2 of 2')).toBeInTheDocument();
    expect(next).toHaveTextContent('It says nowNothing yet.');
    expect(next).toHaveTextContent('An agent picked it up from its work in a session.');
    await user.click(screen.getByRole('button', { name: 'Discard' }));
    expect(await screen.findByText(/Nothing waits for you/)).toBeInTheDocument();
    expect(discard).toHaveBeenCalledWith(
      expect.objectContaining({ id: 'p-2' }),
      'Discarded on the Review page',
    );
  });

  it('reads one layer when the address names it, and says when more wait', async () => {
    const review = vi.fn().mockResolvedValue({ proposals: [PERSONS], more: ['org'] });
    renderEveryday(ReviewPage, '/mimir/review?layer=org', gateway({ review }));
    const card = await screen.findByRole('article', { name: 'On-call' });
    expect(review).toHaveBeenCalledWith('org');
    expect(screen.getByText('1 of 1 or more')).toBeInTheDocument();
    expect(card).toHaveTextContent('A person proposed it.');
  });

  it('says when nothing waits', async () => {
    const review = vi.fn().mockResolvedValue({ proposals: [], more: [] });
    renderEveryday(ReviewPage, '/mimir/review', gateway({ review }));
    expect(await screen.findByText(/Nothing waits for you/)).toBeInTheDocument();
  });

  it('shows a failed review and a failed decision as errors', async () => {
    const user = userEvent.setup();
    const failing = vi.fn().mockRejectedValue(new Error('Forge is down'));
    renderEveryday(ReviewPage, '/mimir/review', gateway({ review: failing }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Forge is down');
    cleanup();
    const keep = vi.fn().mockRejectedValue('already decided');
    renderEveryday(ReviewPage, '/mimir/review', gateway({ keep }));
    await screen.findByRole('article', { name: 'The booking cut-over date' });
    await user.click(screen.getByRole('button', { name: 'Keep' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('already decided');
  });

  it('shows that it is loading', async () => {
    const review = vi.fn().mockReturnValue(new Promise(() => {}));
    renderEveryday(ReviewPage, '/mimir/review', gateway({ review }));
    await waitFor(() => expect(screen.getByText('Loading…')).toBeInTheDocument());
  });
});
