import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { createMockVolundrService } from '../adapters/mock';
import type { IVolundrService } from '../ports/IVolundrService';
import type { SessionParticipant, VolundrSession } from '../models/volundr.model';
import { SessionPeopleButton, SessionPeoplePanel } from './SessionPeople';

const OWNED: VolundrSession = {
  id: 's1',
  name: 'Fix the login',
  source: { type: 'git', repo: 'acme/app', branch: 'main' },
  status: 'running',
  model: 'm',
  lastActive: 0,
  messageCount: 0,
  tokensUsed: 0,
  ownerId: 'u-owner',
  coordination: { projectId: 'p1', role: 'worker' },
};

const SHARED: VolundrSession = {
  ...OWNED,
  source: null,
  ownerName: 'Olivia Owner',
  room: { role: 'viewer', participantStatus: 'active', expiresAt: null },
};

const participant = (overrides: Partial<SessionParticipant> = {}): SessionParticipant => ({
  userId: 'ivan',
  displayName: 'Ivan Invitee',
  role: 'viewer',
  status: 'active',
  invitedBy: 'u-owner',
  expiresAt: null,
  ...overrides,
});

function setup(
  ui: (volundr: IVolundrService) => React.ReactNode,
  options: {
    userId?: string;
    roles?: string[];
    participants?: SessionParticipant[];
    overrides?: Partial<IVolundrService>;
  } = {},
) {
  const volundr = {
    ...createMockVolundrService(),
    getIdentity: vi.fn().mockResolvedValue({
      userId: options.userId ?? 'u-owner',
      email: 'o@x.test',
      tenantId: 't',
      roles: options.roles ?? ['volundr:developer'],
      displayName: 'Olivia Owner',
      status: 'active',
    }),
    getSessionParticipants: vi.fn().mockResolvedValue(options.participants ?? []),
    getSessionProjectPeople: vi.fn().mockResolvedValue([
      { userId: 'pam', displayName: 'Pam Project', projectRole: 'approver', roomRole: 'approver' },
      {
        userId: 'u-owner',
        displayName: 'Olivia Owner',
        projectRole: 'owner',
        roomRole: 'approver',
      },
    ]),
    getParticipantCandidates: vi
      .fn()
      .mockResolvedValue([{ userId: 'zed', displayName: 'Zed Zimmer', email: 'zed@x.test' }]),
    inviteSessionParticipant: vi.fn().mockResolvedValue(participant({ status: 'invited' })),
    removeSessionParticipant: vi.fn().mockResolvedValue(undefined),
    ...options.overrides,
  } as IVolundrService;
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <ServicesProvider services={{ volundr }}>{ui(volundr)}</ServicesProvider>
    </QueryClientProvider>,
  );
  return volundr;
}

describe('SessionPeoplePanel for the owner', () => {
  it("lists the owner, the project's people via project, and the chat's own participants", async () => {
    setup(() => <SessionPeoplePanel session={OWNED} />, {
      participants: [
        participant(),
        participant({
          userId: 'zoe',
          displayName: 'Zoe Zhou',
          role: 'approver',
          status: 'invited',
        }),
      ],
    });

    const owner = await screen.findByTestId('session-person-u-owner');
    expect(owner).toHaveTextContent('Olivia Owner');
    expect(owner).toHaveTextContent('(you)');
    expect(owner).toHaveTextContent('Owner');
    expect(await screen.findByTestId('session-person-pam')).toHaveTextContent(
      'Approver · via project',
    );
    expect(await screen.findByTestId('session-person-ivan')).toHaveTextContent(
      'Viewer · In the chat',
    );
    expect(screen.getByTestId('session-person-zoe')).toHaveTextContent('Approver · Invited');
    // The owner is listed once, not again as a project member.
    expect(screen.getAllByTestId('session-person-u-owner')).toHaveLength(1);
  });

  it("asks for the project's people only when the session has a project", async () => {
    const volundr = setup(() => (
      <SessionPeoplePanel session={{ ...OWNED, coordination: undefined }} />
    ));

    await screen.findByTestId('session-people-invite');
    expect(volundr.getSessionProjectPeople).not.toHaveBeenCalled();
  });

  it('searches the org, then invites the chosen person with the chosen role', async () => {
    const volundr = setup(() => <SessionPeoplePanel session={OWNED} />);
    const search = await screen.findByLabelText('Find someone in your org');

    fireEvent.change(search, { target: { value: 'ze' } });
    fireEvent.click(await screen.findByRole('button', { name: /Zed Zimmer/ }));
    fireEvent.change(screen.getByLabelText('Role for the invitee'), {
      target: { value: 'approver' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Invite' }));

    await waitFor(() =>
      expect(volundr.inviteSessionParticipant).toHaveBeenCalledWith(
        's1',
        { userId: 'zed', role: 'approver' },
        expect.anything(),
      ),
    );
    expect(volundr.getParticipantCandidates).toHaveBeenCalledWith('s1', 'ze', expect.anything());
    await waitFor(() => expect(search).toHaveValue(''));
  });

  it('cannot invite before someone is chosen', async () => {
    setup(() => <SessionPeoplePanel session={OWNED} />);
    const search = await screen.findByLabelText('Find someone in your org');
    fireEvent.change(search, { target: { value: 'ze' } });

    expect(screen.getByRole('button', { name: 'Invite' })).toBeDisabled();
  });

  it('says so when nobody matches', async () => {
    setup(() => <SessionPeoplePanel session={OWNED} />, {
      overrides: { getParticipantCandidates: vi.fn().mockResolvedValue([]) },
    });
    fireEvent.change(await screen.findByLabelText('Find someone in your org'), {
      target: { value: 'nobody' },
    });

    expect(await screen.findByText('Nobody else in your org matches.')).toBeInTheDocument();
  });

  it("shows the server's reason inline when the invitation is refused", async () => {
    setup(() => <SessionPeoplePanel session={OWNED} />, {
      overrides: {
        inviteSessionParticipant: vi
          .fn()
          .mockRejectedValue(Object.assign(new Error('x'), { detail: 'Cannot invite this user' })),
      },
    });
    fireEvent.change(await screen.findByLabelText('Find someone in your org'), {
      target: { value: 'ze' },
    });
    fireEvent.click(await screen.findByRole('button', { name: /Zed Zimmer/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Invite' }));

    expect(await screen.findByText('Cannot invite this user')).toBeInTheDocument();
  });

  it('shows why the search failed', async () => {
    setup(() => <SessionPeoplePanel session={OWNED} />, {
      overrides: { getParticipantCandidates: vi.fn().mockRejectedValue(new Error('no directory')) },
    });
    fireEvent.change(await screen.findByLabelText('Find someone in your org'), {
      target: { value: 'ze' },
    });

    expect(await screen.findByText('no directory')).toBeInTheDocument();
  });

  it('removes a person from the chat, and withdraws an invitation', async () => {
    const volundr = setup(() => <SessionPeoplePanel session={OWNED} />, {
      participants: [
        participant(),
        participant({ userId: 'zoe', displayName: 'Zoe', status: 'invited' }),
      ],
    });

    fireEvent.click(await screen.findByRole('button', { name: 'Remove Ivan Invitee' }));
    fireEvent.click(screen.getByRole('button', { name: 'Remove Zoe' }));

    await waitFor(() =>
      expect(volundr.removeSessionParticipant).toHaveBeenCalledWith(
        's1',
        'ivan',
        expect.anything(),
      ),
    );
    expect(volundr.removeSessionParticipant).toHaveBeenCalledWith('s1', 'zoe', expect.anything());
    expect(screen.getByRole('button', { name: 'Remove Zoe' })).toHaveTextContent('Withdraw');
  });

  it('shows why a removal failed', async () => {
    setup(() => <SessionPeoplePanel session={OWNED} />, {
      participants: [participant()],
      overrides: { removeSessionParticipant: vi.fn().mockRejectedValue(new Error('Not allowed')) },
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Remove Ivan Invitee' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Not allowed');
  });

  it('shows why the list could not be read', async () => {
    setup(() => <SessionPeoplePanel session={OWNED} />, {
      overrides: { getSessionParticipants: vi.fn().mockRejectedValue(new Error('list refused')) },
    });

    expect(await screen.findByText('list refused')).toBeInTheDocument();
  });

  it('lets an admin who is not the owner manage the chat too', async () => {
    setup(() => <SessionPeoplePanel session={OWNED} />, {
      userId: 'u-admin',
      roles: ['volundr:admin'],
    });

    expect(await screen.findByTestId('session-people-invite')).toBeInTheDocument();
  });
});

describe('SessionPeoplePanel for a participant', () => {
  const asParticipant = { userId: 'ivan' };

  it('is a read-only list of who is in the chat, with the owner named', async () => {
    setup(() => <SessionPeoplePanel session={SHARED} />, {
      ...asParticipant,
      participants: [participant()],
    });

    expect(await screen.findByTestId('session-person-u-owner')).toHaveTextContent('Olivia Owner');
    expect(await screen.findByTestId('session-person-ivan')).toHaveTextContent('(you)');
    expect(screen.queryByTestId('session-people-invite')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Remove/ })).not.toBeInTheDocument();
  });

  it('leaves the chat', async () => {
    const onLeft = vi.fn();
    const volundr = setup(() => <SessionPeoplePanel session={SHARED} onLeft={onLeft} />, {
      ...asParticipant,
      participants: [participant()],
    });

    fireEvent.click(await screen.findByRole('button', { name: 'Leave chat' }));

    await waitFor(() => expect(onLeft).toHaveBeenCalledOnce());
    expect(volundr.removeSessionParticipant).toHaveBeenCalledWith('s1', 'ivan', expect.anything());
  });

  it('shows why leaving failed and stays', async () => {
    const onLeft = vi.fn();
    setup(() => <SessionPeoplePanel session={SHARED} onLeft={onLeft} />, {
      ...asParticipant,
      participants: [participant()],
      overrides: {
        removeSessionParticipant: vi.fn().mockRejectedValue(new Error('Could not leave')),
      },
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Leave chat' }));

    expect(await screen.findByText('Could not leave')).toBeInTheDocument();
    expect(onLeft).not.toHaveBeenCalled();
  });

  it('names the owner by id when nothing names them', async () => {
    setup(() => <SessionPeoplePanel session={{ ...SHARED, ownerName: undefined }} />, {
      ...asParticipant,
      participants: [participant()],
    });

    expect(await screen.findByTestId('session-person-u-owner')).toHaveTextContent('u-owner');
  });
});

describe('SessionPeopleButton', () => {
  it('opens the panel for the owner', async () => {
    setup(() => <SessionPeopleButton session={OWNED} />);

    fireEvent.click(await screen.findByRole('button', { name: 'People' }));

    const panel = await screen.findByTestId('session-people');
    expect(within(panel).getByText('Invite someone from your org')).toBeInTheDocument();
  });

  it('is there for a participant, who may leave from it', async () => {
    const onLeft = vi.fn();
    setup(() => <SessionPeopleButton session={SHARED} onLeft={onLeft} />, {
      userId: 'ivan',
      participants: [participant()],
    });

    fireEvent.click(await screen.findByRole('button', { name: 'People' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Leave chat' }));

    await waitFor(() => expect(onLeft).toHaveBeenCalledOnce());
  });

  it('is not there for someone who can read the session but has no say over its people', async () => {
    const volundr = setup(() => <SessionPeopleButton session={OWNED} />, { userId: 'u-viewer' });

    await waitFor(() => expect(volundr.getIdentity).toHaveBeenCalled());
    expect(screen.queryByRole('button', { name: 'People' })).not.toBeInTheDocument();
  });
});
