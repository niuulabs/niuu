import { describe, expect, it } from 'vitest';
import { orderCards, type RealmHomeCard } from './useRealmsHome';

function card(
  name: string,
  overrides: Partial<Pick<RealmHomeCard, 'resident' | 'ravn' | 'pendingReviews'>> = {},
): RealmHomeCard {
  return {
    realm: { slug: name.toLowerCase(), name } as RealmHomeCard['realm'],
    resident: null,
    ravn: null,
    environment: null,
    binding: null,
    pendingReviews: [],
    runningSessions: 0,
    totalSessions: 0,
    ...overrides,
  };
}

describe('orderCards', () => {
  it('puts realms that need you first, then awake residents, then fleet-only, then the rest', () => {
    const review = { itemId: 'r' } as RealmHomeCard['pendingReviews'][number];
    const ordered = orderCards([
      card('Zeta'),
      card('Fleet', { ravn: { id: 'x' } as RealmHomeCard['ravn'] }),
      card('Awake', { resident: { wakefulness: 'wakeful' } as RealmHomeCard['resident'] }),
      card('Asleep', { resident: { wakefulness: 'sleeping' } as RealmHomeCard['resident'] }),
      card('Asks', { pendingReviews: [review] }),
      card('Alpha'),
    ]);
    expect(ordered.map((entry) => entry.realm.name)).toEqual([
      'Asks',
      'Awake',
      'Asleep',
      'Fleet',
      'Alpha',
      'Zeta',
    ]);
  });
});
