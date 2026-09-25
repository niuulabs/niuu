import { describe, expect, it } from 'vitest';
import {
  isSessionRavn,
  ravnLifeState,
  ravnReasonLine,
  ravnTarget,
  sessionBackedRavens,
} from './ravnWorkbench';
import { ravnSessionRequest, sessionNameFor } from './sessionLaunch';
import { RAVN_ID, makeRavn, makeSession } from '../testing/fixtures';

const FLOCK = '77777777-7777-4777-8777-777777777777';

describe('sessionBackedRavens', () => {
  it('turns a live session no listed ravn owns into a ravn', () => {
    const [ravn, ...rest] = sessionBackedRavens(
      [
        makeSession({
          id: FLOCK,
          ravnId: FLOCK,
          title: 'huginn-scout',
          model: '',
          instanceId: undefined,
          messageCount: 3,
          tokenCount: 900,
          costUsd: 0.01,
          flockId: '88888888-8888-4888-8888-888888888888',
          flockRole: 'coordinator',
        }),
      ],
      [makeRavn()],
    );
    expect(rest).toHaveLength(0);
    expect(ravn).toMatchObject({
      id: FLOCK,
      residentName: 'huginn-scout',
      personaName: '',
      kind: 'session',
      managed: false,
      engine: 'ravn',
      status: 'active',
      observedState: 'active',
      sessionId: FLOCK,
      messageCount: 3,
      tokenCount: 900,
      costUsd: 0.01,
      flockRole: 'coordinator',
    });
    expect(isSessionRavn(ravn!)).toBe(true);
    expect(ravnTarget(ravn!)).toBe('this Forge');
  });

  it('leaves sessions that belong to a listed ravn alone', () => {
    expect(sessionBackedRavens([makeSession({ ravnId: RAVN_ID })], [makeRavn()])).toEqual([]);
  });

  it('reads a session that is not running yet as starting', () => {
    const [ravn] = sessionBackedRavens(
      [makeSession({ id: FLOCK, ravnId: FLOCK, status: 'idle', instanceName: 'Local Forge' })],
      [],
    );
    expect(ravnLifeState(ravn!)).toBe('starting');
    expect(ravnReasonLine(ravn!)).toBe('Deploying on Local Forge…');
  });

  it('keeps one ravn per session owner, preferring the running session', () => {
    const ravens = sessionBackedRavens(
      [
        makeSession({ id: 'a', ravnId: FLOCK, status: 'idle' }),
        makeSession({ id: 'b', ravnId: FLOCK, status: 'running' }),
        makeSession({ id: 'c', ravnId: FLOCK, status: 'idle' }),
      ],
      [],
    );
    expect(ravens).toHaveLength(1);
    expect(ravens[0]!.sessionId).toBe('b');
  });

  it('names a resident target plainly', () => {
    expect(ravnTarget(makeRavn({ instanceName: '', backend: undefined }))).toBe('unassigned');
  });
});

describe('ravn session launch', () => {
  it('makes a Forge-valid session name', () => {
    expect(sessionNameFor('Huginn Scout')).toBe('huginn-scout');
    expect(sessionNameFor('  --Ravn__#1!--  ')).toBe('ravn-1');
    expect(sessionNameFor('!!!')).toBe('');
    expect(sessionNameFor('a'.repeat(80))).toHaveLength(63);
  });

  it('asks Forge for a one-persona flock with its own workspace', () => {
    expect(ravnSessionRequest({ name: 'Huginn Scout', persona: 'research-analyst' })).toEqual({
      name: 'huginn-scout',
      model: '',
      source: { type: 'git', repo: '', branch: 'main' },
      workloadType: 'ravn_flock',
      workloadConfig: { personas: ['research-analyst'] },
    });
  });
});
