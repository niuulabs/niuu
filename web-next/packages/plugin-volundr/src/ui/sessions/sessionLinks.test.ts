import { describe, expect, it, vi } from 'vitest';
import type { Session } from '../../domain/session';
import { buildSessionLinks, sessionHref, sessionLinkTone } from './sessionLinks';

const session = (id: string, name: string | undefined, state: Session['state']): Session =>
  ({ id, name, personaName: `persona-${id}`, state, clusterId: 'local' }) as Session;

describe('buildSessionLinks', () => {
  const list = [
    session('s1', 'ticket-1342-gates', 'running'),
    session('s2', 'ticket-1339-catalog', 'awaiting_input'),
    session('s3', undefined, 'failed'),
    session('s4', 'twin', 'idle'),
    session('s5', 'twin', 'idle'),
    session('self', 'plan-review-coordinator', 'running'),
  ];

  it('resolves a session by id or by name with its state, tone and href', () => {
    const links = buildSessionLinks(list, 'self', vi.fn());
    expect(links.resolve('s1')).toEqual({
      id: 's1',
      name: 'ticket-1342-gates',
      stateLabel: 'Working',
      tone: 'ok',
      href: '/volundr/sessions/s1',
    });
    expect(links.resolve('ticket-1339-catalog')).toMatchObject({
      id: 's2',
      stateLabel: 'Needs you',
      tone: 'warn',
    });
    expect(links.resolve('s3')).toMatchObject({
      name: 'persona-s3',
      stateLabel: 'Error',
      tone: 'critical',
    });
    expect(links.resolve('s4')).toMatchObject({ stateLabel: 'Idle', tone: 'muted' });
  });

  it('does not guess between sessions sharing a name, and never links the session itself', () => {
    const links = buildSessionLinks(list, 'self', vi.fn());
    expect(links.resolve('twin')).toBeNull();
    expect(links.resolve('self')).toBeNull();
    expect(links.resolve('plan-review-coordinator')).toBeNull();
    expect(links.resolve('nobody')).toBeNull();
  });

  it('opens through the page’s navigation', () => {
    const open = vi.fn();
    buildSessionLinks(list, 'self', open).open('s1');
    expect(open).toHaveBeenCalledWith('s1');
  });

  it('encodes ids in hrefs and maps states to tones', () => {
    expect(sessionHref('a/b')).toBe('/volundr/sessions/a%2Fb');
    expect(sessionLinkTone({ state: 'running' })).toBe('ok');
    expect(sessionLinkTone({ state: 'archived' })).toBe('muted');
  });
});
