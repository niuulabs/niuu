import { describe, expect, it } from 'vitest';
import { classifyRelayedTurn, isLongRelay } from './relayedTurn';

const UUID = '3f1c2d4e-5a6b-4c7d-8e9f-0a1b2c3d4e5f';

describe('classifyRelayedTurn', () => {
  it('reads the Guild header with the sender name, id and host', () => {
    const relay = classifyRelayedTurn(
      `[Message from session ticket-1339-catalog (${UUID}) on Valhalla, relayed through the Guild]\n\nTwo retired models are pinned.`,
    );
    expect(relay).toMatchObject({
      kind: 'guild',
      tag: 'via Guild',
      senderName: 'ticket-1339-catalog',
      senderSessionId: UUID,
      host: 'Valhalla',
      body: 'Two retired models are pinned.',
      needsKnownSender: false,
    });
  });

  it('reads the unnamed Guild header, which carries only the session id', () => {
    const relay = classifyRelayedTurn(
      `[Message from session ${UUID.toUpperCase()} on Ymir, relayed through the Guild]\n\nDone.`,
    );
    expect(relay).toMatchObject({
      kind: 'guild',
      senderName: null,
      senderSessionId: UUID,
      host: 'Ymir',
      body: 'Done.',
    });
  });

  it('reads a Guild header whose sender is a bare name', () => {
    const relay = classifyRelayedTurn(
      '[Message from session coder on Ymir, relayed through the Guild]\n\nHi',
    );
    expect(relay).toMatchObject({ senderName: 'coder', senderSessionId: null, host: 'Ymir' });
  });

  it('is not a Guild header without the closing words or a host', () => {
    expect(classifyRelayedTurn('[Message from session coder on Ymir]\n\nHi')).toBeNull();
    expect(
      classifyRelayedTurn('[Message from session coder, relayed through the Guild]\n\nHi'),
    ).toBeNull();
  });

  it('reads the web Forward header and strips it from the body', () => {
    const relay = classifyRelayedTurn(
      'Forwarded from plan-review-coordinator:\n\nPlease check 1342.',
    );
    expect(relay).toMatchObject({
      kind: 'forwarded',
      tag: 'forwarded',
      senderName: 'plan-review-coordinator',
      host: null,
      body: 'Please check 1342.',
      needsKnownSender: true,
    });
  });

  it.each([
    ['From ios-coordinator:\n\nShip it.', 'ios-coordinator', null, 'Ship it.'],
    ['From ios-coordinator (Valhalla):\n\nShip it.', 'ios-coordinator', 'Valhalla', 'Ship it.'],
    ['From ios-coordinator, ship it.', 'ios-coordinator', null, 'ship it.'],
    [
      'From ios-coordinator. Next steps below.\n\n- one',
      'ios-coordinator',
      null,
      'Next steps below.\n- one',
    ],
    ['From ios-coordinator — a note', 'ios-coordinator', null, 'a note'],
  ])('reads %j as a session header', (text, name, host, body) => {
    const relay = classifyRelayedTurn(text);
    expect(relay).toMatchObject({
      kind: 'session',
      tag: 'session',
      senderName: name,
      host,
      body,
      needsKnownSender: true,
    });
  });

  it('ignores leading blank lines before the header', () => {
    expect(classifyRelayedTurn('\n\n  From coder:\nhello')).toMatchObject({
      senderName: 'coder',
      body: 'hello',
    });
  });

  it.each([
    'Also link NIU-1342 in the PR body.',
    'From v1.2.3 onwards the API changes.',
    'From the top: do it again',
    'Please read this.\n\nFrom coder:\nnot the first line',
    'forwarded from coder: lowercase is not the header',
    '[Message from session coder on Ymir]',
    '',
  ])('leaves %j as an ordinary message', (text) => {
    expect(classifyRelayedTurn(text)).toBeNull();
  });
});

describe('isLongRelay', () => {
  it('collapses past twelve non-empty lines', () => {
    expect(isLongRelay(Array.from({ length: 12 }, (_, i) => `l${i}`).join('\n'))).toBe(false);
    expect(isLongRelay(Array.from({ length: 13 }, (_, i) => `l${i}`).join('\n'))).toBe(true);
  });

  it('does not count blank lines', () => {
    expect(isLongRelay(Array.from({ length: 30 }, () => '').join('\n'))).toBe(false);
  });

  it('collapses past 1200 characters', () => {
    expect(isLongRelay('x'.repeat(1200))).toBe(false);
    expect(isLongRelay('x'.repeat(1201))).toBe(true);
  });
});
