import { describe, it, expect, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { MimirTopbar } from './MimirTopbar';
import { renderWithMimir } from '../testing/renderWithMimir';
import { createMimirMockAdapter } from '../adapters/mock';
import type { IMimirService } from '../ports';
import type { PluginCtx } from '@niuulabs/plugin-sdk';

const mockCtx: PluginCtx = {
  tweaks: {},
  setTweak: vi.fn(),
};

const wrap = (ctx = mockCtx, service?: IMimirService) =>
  renderWithMimir(<MimirTopbar ctx={ctx} />, service);

describe('MimirTopbar', () => {
  it('renders the mount label', () => {
    wrap();
    expect(screen.getByText('mount')).toBeInTheDocument();
  });

  it('shows "all mounts" when activeMount is not set', () => {
    wrap();
    expect(screen.getByText('all mounts')).toBeInTheDocument();
  });

  it('shows the activeMount name when set in tweaks', () => {
    wrap({ tweaks: { activeMount: 'local' }, setTweak: vi.fn() });
    expect(screen.getByText('local')).toBeInTheDocument();
  });

  it('renders page count stat', async () => {
    wrap();
    await waitFor(() => expect(screen.getByText('pages')).toBeInTheDocument());
  });

  it('renders wardens stat label', async () => {
    wrap();
    await waitFor(() => expect(screen.getByText('wardens')).toBeInTheDocument());
  });

  it('renders lint stat label', async () => {
    wrap();
    await waitFor(() => expect(screen.getByText('lint')).toBeInTheDocument());
  });

  it('lint count has warn class when there are issues', async () => {
    wrap();
    await waitFor(() => {
      const lintVal = screen.getByLabelText(/lint issues/);
      expect(lintVal.className).toContain('--warn');
    });
  });

  it('lint count has no warn class when there are zero issues', async () => {
    const noLint: IMimirService = {
      ...createMimirMockAdapter(),
      lint: {
        ...createMimirMockAdapter().lint,
        getLintReport: async () => ({
          issues: [],
          pagesChecked: 0,
          summary: { error: 0, warn: 0, info: 0 },
        }),
      },
    };
    wrap(mockCtx, noLint);
    await waitFor(() => {
      const lintVal = screen.getByLabelText(/lint issues/);
      expect(lintVal.className).not.toContain('--warn');
    });
  });

  it('renders the aria label for the stats container', () => {
    wrap();
    expect(screen.getByLabelText('Mímir stats')).toBeInTheDocument();
  });
});
