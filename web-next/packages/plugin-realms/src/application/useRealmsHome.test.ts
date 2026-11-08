import { describe, expect, it } from 'vitest';
import { needsYouCount, orderCards, ticketsOf, type RealmHomeCard } from './useRealmsHome';

function card(
  name: string,
  overrides: Partial<Pick<RealmHomeCard, 'waitingTickets' | 'runningSessions'>> = {},
): RealmHomeCard {
  return {
    realm: { slug: name.toLowerCase(), name } as RealmHomeCard['realm'],
    binding: null,
    boardName: null,
    waitingTickets: [],
    runningSessions: 0,
    totalSessions: 0,
    ...overrides,
  };
}

const ticket = { taskId: 't' } as RealmHomeCard['waitingTickets'][number];

describe('orderCards', () => {
  it('puts workspaces with a ticket waiting first, then the busy ones, then by name', () => {
    const ordered = orderCards([
      card('Zeta'),
      card('Busy', { runningSessions: 2 }),
      card('Asks', { waitingTickets: [ticket] }),
      card('Alpha'),
    ]);
    expect(ordered.map((entry) => entry.realm.name)).toEqual(['Asks', 'Busy', 'Alpha', 'Zeta']);
  });
});

describe('needsYouCount and ticketsOf', () => {
  it('counts the tickets waiting on a card and lists them with their card', () => {
    const asking = card('Asks', { waitingTickets: [ticket, ticket] });
    const quiet = card('Quiet');
    expect(needsYouCount(asking)).toBe(2);
    expect(needsYouCount(quiet)).toBe(0);
    expect(ticketsOf([asking, quiet]).map((entry) => entry.card.realm.name)).toEqual([
      'Asks',
      'Asks',
    ]);
  });
});
