import { describe, expect, it } from 'vitest';
import { COORDINATOR_LABEL, isCoordinator, speakerFor } from './speaker';

const host = { peerId: 'coder-session', persona: 'Skuld', participantType: 'skuld' };
const member = {
  peerId: 'reviewer-session',
  persona: 'reviewer',
  displayName: 'wf-run-1-reviewer',
  participantType: 'skuld',
};

describe('speaker', () => {
  it('makes every Skuld participant the lead when the room names no host', () => {
    expect(isCoordinator(member)).toBe(true);
    expect(speakerFor(member).name).toBe(COORDINATOR_LABEL);
  });

  it("names a workflow run's other sessions as themselves and the host by its role", () => {
    const room = { peerId: 'coder-session', label: 'Coder' };

    expect(isCoordinator(host, room)).toBe(true);
    expect(speakerFor(host, room)).toMatchObject({ name: 'Coder', coordinator: true });
    expect(isCoordinator(member, room)).toBe(false);
    expect(speakerFor(member, room)).toMatchObject({
      name: 'wf-run-1-reviewer',
      coordinator: false,
    });
  });
});
