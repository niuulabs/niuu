import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createMockVolundrService, type IVolundrService } from '@niuulabs/plugin-volundr';
import { renderRealms } from '../testing/renderRealms';
import { canManageMembers } from '../application/useProjectMembers';

const PAGE = '/realms/projects/project-yggdrasil';

describe('ProjectMembers', () => {
  afterEach(() => {
    cleanup();
    localStorage.clear();
  });

  it('shows the owner and lets them add someone from the org with a role', async () => {
    const user = userEvent.setup();
    renderRealms(PAGE);
    const members = await screen.findByTestId('project-members');
    expect(await within(members).findByTestId('project-member-u1')).toHaveTextContent('Dev');
    expect(within(members).getByText('(you)')).toBeInTheDocument();

    await user.type(
      within(members).getByRole('textbox', { name: 'Find someone in your org' }),
      'ali',
    );
    await user.click(await within(members).findByRole('button', { name: /Alice Lindqvist/ }));
    await user.selectOptions(
      within(members).getByRole('combobox', { name: 'Role for the new member' }),
      'approver',
    );
    await user.click(within(members).getByRole('button', { name: 'Add' }));

    const alice = await within(members).findByTestId('project-member-alice');
    expect(within(alice).getByRole('combobox', { name: 'Role of Alice Lindqvist' })).toHaveValue(
      'approver',
    );
    expect(screen.getByRole('heading', { name: /Members · 2/ })).toBeInTheDocument();
  });

  it('says when nobody else in the org matches', async () => {
    const user = userEvent.setup();
    renderRealms(PAGE);
    const members = await screen.findByTestId('project-members');
    await user.type(
      within(members).getByRole('textbox', { name: 'Find someone in your org' }),
      'zzz',
    );
    expect(
      await within(members).findByText('Nobody else in your org matches.'),
    ).toBeInTheDocument();
  });

  it('changes a role, edits expertise and removes a member', async () => {
    const user = userEvent.setup();
    const volundr = createMockVolundrService();
    const [project] = (await volundr.getProjects()).filter((p) => p.id === 'project-yggdrasil');
    await volundr.addProjectMember(project!, { userId: 'bram', role: 'viewer' });
    renderRealms(PAGE, { volundr });
    const bram = await screen.findByTestId('project-member-bram');

    await user.selectOptions(
      within(bram).getByRole('combobox', { name: 'Role of Bram Koster' }),
      'contributor',
    );
    await waitFor(async () =>
      expect(
        (await volundr.getProjectMembers(project!)).find((m) => m.user_id === 'bram')?.role,
      ).toBe('contributor'),
    );

    await user.click(within(bram).getByRole('button', { name: 'Edit expertise of Bram Koster' }));
    await user.type(
      within(bram).getByRole('textbox', { name: 'Expertise of Bram Koster' }),
      'Ting',
    );
    await user.click(within(bram).getByRole('button', { name: 'Save' }));
    expect(await within(bram).findByText('Ting')).toBeInTheDocument();

    await user.click(within(bram).getByRole('button', { name: 'Remove Bram Koster' }));
    await waitFor(() =>
      expect(screen.queryByTestId('project-member-bram')).not.toBeInTheDocument(),
    );
  });

  it('shows an error when the last owner would be demoted', async () => {
    const user = userEvent.setup();
    renderRealms(PAGE);
    const me = await screen.findByTestId('project-member-u1');
    await user.selectOptions(within(me).getByRole('combobox', { name: 'Role of Dev' }), 'viewer');
    expect(await within(me).findByText(/at least one owner/)).toBeInTheDocument();
  });

  it('shows roles read-only to a member who is not an owner', async () => {
    const base = createMockVolundrService();
    const volundr: IVolundrService = {
      ...base,
      getIdentity: async () => ({ ...(await base.getIdentity()), userId: 'alice' }),
      getProjectMembers: async () => [
        {
          project_id: 'project-yggdrasil',
          user_id: 'u1',
          role: 'owner',
          expertise: 'Platform',
          skills: [{ id: 'k8s', name: 'Kubernetes' }],
          display_name: 'Dev',
          email: 'dev@niuu.world',
        },
        {
          project_id: 'project-yggdrasil',
          user_id: 'alice',
          role: 'viewer',
          expertise: '',
          skills: [],
          display_name: '',
          email: 'alice@example.com',
        },
      ],
    };
    renderRealms(PAGE, { volundr });
    const members = await screen.findByTestId('project-members');
    const dev = await within(members).findByTestId('project-member-u1');
    expect(within(dev).getByText('Owner')).toBeInTheDocument();
    expect(within(dev).getByText('Kubernetes')).toBeInTheDocument();
    expect(within(members).queryByTestId('project-member-add')).not.toBeInTheDocument();
    const alice = within(members).getByTestId('project-member-alice');
    expect(alice).toHaveTextContent('alice@example.com');
    expect(within(alice).getByRole('button', { name: /Edit expertise/ })).toBeInTheDocument();
    expect(within(alice).queryByRole('button', { name: /Remove/ })).not.toBeInTheDocument();
  });

  it('shows a load error', async () => {
    const volundr: IVolundrService = {
      ...createMockVolundrService(),
      getProjectMembers: async () => {
        throw new Error('members unavailable');
      },
    };
    renderRealms(PAGE, { volundr });
    expect(await screen.findByText(/members unavailable/)).toBeInTheDocument();
  });
});

describe('canManageMembers', () => {
  const owner = {
    project_id: 'p',
    user_id: 'u1',
    role: 'owner' as const,
    expertise: '',
    skills: [],
    display_name: '',
    email: '',
  };
  it('lets owners and tenant admins manage, and nobody without an identity', () => {
    expect(canManageMembers([owner], { userId: 'u1', roles: [] })).toBe(true);
    expect(canManageMembers([owner], { userId: 'u2', roles: [] })).toBe(false);
    expect(canManageMembers([owner], { userId: 'u2', roles: ['volundr:admin'] })).toBe(true);
    expect(canManageMembers([owner], undefined)).toBe(false);
  });
});
