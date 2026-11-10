import { describe, expect, it } from 'vitest';
import {
  canAnswerGates,
  isInvitation,
  isSharedSession,
  roomRoleOfProjectRole,
  sharedSessionLabel,
} from './volundr.model';
import type { SessionRoom } from './volundr.model';

const room = (role: SessionRoom['role']): SessionRoom => ({
  role,
  participantStatus: 'active',
  expiresAt: null,
});

describe('shared session helpers', () => {
  it('a full session is not shared and may answer gates', () => {
    expect(isSharedSession({})).toBe(false);
    expect(canAnswerGates({})).toBe(true);
  });

  it("the owner's own room view is not shared and may answer gates", () => {
    expect(isSharedSession({ room: room('owner') })).toBe(false);
    expect(canAnswerGates({ room: room('owner') })).toBe(true);
  });

  it('a viewer shares the session and may not answer gates', () => {
    expect(isSharedSession({ room: room('viewer') })).toBe(true);
    expect(canAnswerGates({ room: room('viewer') })).toBe(false);
  });

  it('an approver shares the session and may answer gates', () => {
    expect(isSharedSession({ room: room('approver') })).toBe(true);
    expect(canAnswerGates({ room: room('approver') })).toBe(true);
  });

  it('labels a shared session with the role', () => {
    expect(sharedSessionLabel(room('approver'))).toBe('Shared · approver');
    expect(sharedSessionLabel(room('viewer'))).toBe('Shared · viewer');
  });
});

describe('invitations', () => {
  const invited = (role: SessionRoom['role']): SessionRoom => ({
    role,
    participantStatus: 'invited',
    expiresAt: null,
  });

  it('is an invitation only while the grant is unanswered', () => {
    expect(isInvitation({ room: invited('viewer') })).toBe(true);
    expect(isInvitation({ room: room('viewer') })).toBe(false);
    expect(isInvitation({})).toBe(false);
  });

  it('gives an invitee no say over gates, whatever role was offered', () => {
    expect(canAnswerGates({ room: invited('approver') })).toBe(false);
    expect(canAnswerGates({ room: invited('viewer') })).toBe(false);
  });
});

describe('roomRoleOfProjectRole', () => {
  it.each([
    ['viewer', 'viewer'],
    ['contributor', 'viewer'],
    ['approver', 'approver'],
    ['owner', 'approver'],
  ] as const)('a project %s is a room %s', (projectRole, roomRole) => {
    expect(roomRoleOfProjectRole(projectRole)).toBe(roomRole);
  });
});
