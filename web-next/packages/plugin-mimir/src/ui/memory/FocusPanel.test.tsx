import { describe, it, expect, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithMimir } from '../../testing/renderWithMimir';
import { createFakeMimirService, FAKE_PAGES, FAKE_GRAPH } from '../../testing/fakeMimirService';
import { FocusPanel } from './FocusPanel';

const PAGE = FAKE_PAGES.find((p) => p.path === '/platform/gateway-routing')!;

function setup(overrides: Partial<React.ComponentProps<typeof FocusPanel>> = {}) {
  const props: React.ComponentProps<typeof FocusPanel> = {
    page: PAGE,
    isLoading: false,
    isError: false,
    graph: FAKE_GRAPH,
    depth: 1,
    disputedIds: new Set<string>(),
    onDepthChange: vi.fn(),
    onClearFocus: vi.fn(),
    onFocusPage: vi.fn(),
    onReadPage: vi.fn(),
    onAskAbout: vi.fn(),
    ...overrides,
  };
  renderWithMimir(<FocusPanel {...props} />, createFakeMimirService());
  return props;
}

describe('FocusPanel', () => {
  it('shows a loading state', () => {
    setup({ isLoading: true, page: null });
    expect(screen.getByText('loading page…')).toBeInTheDocument();
  });

  it('shows an error state and can clear focus from it', async () => {
    const props = setup({ isError: true, page: null });
    await userEvent.click(screen.getByRole('button', { name: 'All memory' }));
    expect(props.onClearFocus).toHaveBeenCalled();
  });

  it('renders breadcrumb, eyebrow, title, and counts', async () => {
    setup();
    expect(screen.getByText('Gateway routing on ymir')).toBeInTheDocument();
    expect(screen.getByText('topic · platform')).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByText(/3 facts · 3 links · rewritten/)).toBeInTheDocument(),
    );
  });

  it('renders every key fact and quotes proof for the facts with evidence', async () => {
    setup();
    expect(
      screen.getByText(
        'The route tables live in values-cedar.yaml, which wins over values-niuu.yaml.',
      ),
    ).toBeInTheDocument();
    await waitFor(() => expect(screen.getAllByTestId('proof-pill')).toHaveLength(3));
  });

  it('calls onReadPage / onAskAbout from the action buttons', async () => {
    const props = setup();
    await userEvent.click(screen.getByRole('button', { name: 'Read the page' }));
    expect(props.onReadPage).toHaveBeenCalledWith(PAGE);
    await userEvent.click(screen.getByRole('button', { name: 'Ask about this' }));
    expect(props.onAskAbout).toHaveBeenCalledWith(PAGE);
  });

  it('lists depth-1 links with relation labels', async () => {
    setup();
    await waitFor(() => expect(screen.getByText('Cedar authorization')).toBeInTheDocument());
    expect(screen.getByText('Cedar authorization').closest('button')).toHaveTextContent(
      'depends on',
    );
  });

  it('marks a disputed linked page in amber', async () => {
    setup({ disputedIds: new Set(['/platform/guilds-gateway']) });
    await waitFor(() => expect(screen.getByText("Guild's gateway")).toBeInTheDocument());
    const row = screen.getByText("Guild's gateway").closest('button')!;
    expect(row).toHaveTextContent('disputed');
  });

  it('changes depth via the segmented control', async () => {
    const props = setup();
    await userEvent.click(screen.getByRole('button', { name: '2' }));
    expect(props.onDepthChange).toHaveBeenCalledWith(2);
  });

  it('focuses a linked page on click', async () => {
    const props = setup();
    await waitFor(() => expect(screen.getByText('Cedar authorization')).toBeInTheDocument());
    await userEvent.click(screen.getByText('Cedar authorization'));
    expect(props.onFocusPage).toHaveBeenCalledWith('/platform/cedar-authorization');
  });

  it('shows a message when there are no linked pages', async () => {
    const other = FAKE_PAGES.find((p) => p.path === '/shared/volundr')!;
    setup({ page: other });
    await waitFor(() => expect(screen.getByText('No linked pages.')).toBeInTheDocument());
  });
});
