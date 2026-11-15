import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { createMockVolundrService } from '../adapters/mock';
import type { IVolundrService } from '../ports/IVolundrService';
import {
  InvitationActions,
  InvitationPane,
  invitationLabel,
  type InvitationSession,
} from './SessionInvitation';

const INVITATION: InvitationSession = {
  id: 's1',
  name: 'Fix the login',
  ownerId: 'u-owner',
  ownerName: 'Olivia Owner',
  room: { role: 'approver', participantStatus: 'invited', expiresAt: null },
};

function setup(ui: React.ReactNode, overrides: Partial<IVolundrService> = {}) {
  const volundr = {
    ...createMockVolundrService(),
    getIdentity: vi.fn().mockResolvedValue({
      userId: 'ivan',
      email: 'i@x.test',
      tenantId: 't',
      roles: [],
      displayName: 'Ivan',
      status: 'active',
    }),
    acceptSessionInvitation: vi.fn().mockResolvedValue({}),
    removeSessionParticipant: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  } as IVolundrService;
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <ServicesProvider services={{ volundr }}>{ui}</ServicesProvider>
    </QueryClientProvider>,
  );
  return volundr;
}

describe('invitationLabel', () => {
  it('says who invited the person and as what', () => {
    expect(invitationLabel(INVITATION)).toBe('Invited by Olivia Owner · approver');
  });

  it('falls back to the owner id, and says only the role when nobody is known', () => {
    expect(invitationLabel({ ...INVITATION, ownerName: undefined })).toBe(
      'Invited by u-owner · approver',
    );
    expect(invitationLabel({ id: 's', room: undefined })).toBe('Invited · viewer');
  });
});

describe('InvitationActions', () => {
  it('joins, then tells the list', async () => {
    const onJoined = vi.fn();
    const volundr = setup(<InvitationActions session={INVITATION} onJoined={onJoined} />);

    fireEvent.click(screen.getByRole('button', { name: 'Join' }));

    await waitFor(() => expect(onJoined).toHaveBeenCalledOnce());
    expect(volundr.acceptSessionInvitation).toHaveBeenCalledWith('s1', expect.anything());
  });

  it("declines by removing the person's own grant, then tells the list", async () => {
    const onDeclined = vi.fn();
    const volundr = setup(<InvitationActions session={INVITATION} onDeclined={onDeclined} />);

    const decline = await screen.findByRole('button', { name: 'Decline' });
    await waitFor(() => expect(decline).toBeEnabled());
    fireEvent.click(decline);

    await waitFor(() => expect(onDeclined).toHaveBeenCalledOnce());
    expect(volundr.removeSessionParticipant).toHaveBeenCalledWith('s1', 'ivan', expect.anything());
    expect(volundr.acceptSessionInvitation).not.toHaveBeenCalled();
  });

  it('shows why joining failed, and does not open the chat', async () => {
    const onJoined = vi.fn();
    setup(<InvitationActions session={INVITATION} onJoined={onJoined} />, {
      acceptSessionInvitation: vi
        .fn()
        .mockRejectedValue(
          Object.assign(new Error('x'), { detail: 'This invitation has expired' }),
        ),
    });
    fireEvent.click(screen.getByRole('button', { name: 'Join' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('This invitation has expired');
    expect(onJoined).not.toHaveBeenCalled();
  });

  it('shows why declining failed', async () => {
    setup(<InvitationActions session={INVITATION} />, {
      removeSessionParticipant: vi.fn().mockRejectedValue(new Error('No grant')),
    });
    const decline = await screen.findByRole('button', { name: 'Decline' });
    await waitFor(() => expect(decline).toBeEnabled());
    fireEvent.click(decline);

    expect(await screen.findByRole('alert')).toHaveTextContent('No grant');
  });

  it('does not select the list row it sits under', async () => {
    const select = vi.fn();
    setup(
      <div onClick={select}>
        <InvitationActions session={INVITATION} stopPropagation />
      </div>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Join' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Join' })).toBeEnabled());

    expect(select).not.toHaveBeenCalled();
  });
});

describe('InvitationPane', () => {
  it('names the chat and the invitation, and offers both answers', () => {
    setup(<InvitationPane session={INVITATION} onJoined={vi.fn()} onDeclined={vi.fn()} />);

    const pane = screen.getByTestId('invitation-pane');
    expect(pane).toHaveTextContent('Fix the login');
    expect(pane).toHaveTextContent('Invited by Olivia Owner · approver');
    expect(screen.getByRole('button', { name: 'Join' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Decline' })).toBeInTheDocument();
  });
});
