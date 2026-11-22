import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, screen } from '@testing-library/react';
import { createMockVolundrService, type ProjectTeamState } from '@niuulabs/plugin-volundr';
import { renderRealms } from '../testing/renderRealms';

const PAGE = '/realms/projects/project-yggdrasil';

function show(state: Partial<ProjectTeamState> | null | Error, sync = {}) {
  renderRealms(PAGE, {
    volundr: {
      ...createMockVolundrService(),
      getProjectTeamState: async () => {
        if (state instanceof Error) throw state;
        if (state === null) return null;
        return {
          home: true,
          home_instance_id: '',
          guild: true,
          ...state,
          sync: { pending: false, synced_at: null, error: '', ...sync },
        };
      },
    },
  });
}

describe('ProjectTeamNotice', () => {
  afterEach(cleanup);

  it.each([
    ['every cluster has the team', {}, {}],
    ['there is one cluster', { guild: false }, { pending: true }],
    ['the Forge is from before shared teams', null, {}],
  ])('says nothing when %s', async (_, state, sync) => {
    show(state, sync);
    await screen.findByTestId('project-members');
    await screen.findByTestId('project-integrations');
    expect(screen.queryByTestId('project-team-notice')).not.toBeInTheDocument();
  });

  it('says when the other clusters are behind this one', async () => {
    show({}, { pending: true, error: 'The Guild could not be reached' });
    expect(await screen.findByTestId('project-team-notice')).toHaveTextContent(
      'Your other clusters do not have the latest team yet. The Guild could not be reached',
    );
  });

  it('says when this cluster may be behind', async () => {
    show({ home: false, home_instance_id: 'ymir' }, { error: 'The Guild could not be reached' });
    expect(await screen.findByTestId('project-team-notice')).toHaveTextContent(
      'The team below may be out of date on this cluster.',
    );
  });

  it('says when it could not be read', async () => {
    show(new Error('API request failed: 500'));
    expect(
      await screen.findByTestId('project-team-notice', {}, { timeout: 5000 }),
    ).toHaveTextContent('API request failed: 500');
  });
});
