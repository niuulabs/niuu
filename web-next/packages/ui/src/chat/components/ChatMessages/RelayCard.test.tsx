import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { UserMessage } from './ChatMessages';
import { SessionLinksProvider, type SessionLink, type SessionLinksValue } from '../../sessionLinks';
import type { ChatMessage } from '../../types';

const now = new Date('2026-11-05T09:31:00Z');
const UUID = '3f1c2d4e-5a6b-4c7d-8e9f-0a1b2c3d4e5f';

afterEach(() => {
  localStorage.clear();
});

const user = (content: string, extra: Partial<ChatMessage> = {}): ChatMessage => ({
  id: 'u1',
  role: 'user',
  content,
  createdAt: now,
  ...extra,
});

const catalog: SessionLink = {
  id: UUID,
  name: 'ticket-1339-catalog',
  stateLabel: 'Needs you',
  tone: 'warn',
  href: `/volundr/sessions/${UUID}`,
};

function links(open = vi.fn()): SessionLinksValue {
  return {
    resolve: (key) => (key === UUID || key === 'ticket-1339-catalog' ? catalog : null),
    open,
  };
}

const guild = `[Message from session ticket-1339-catalog (${UUID}) on Valhalla, relayed through the Guild]\n\nTwo retired models are *still* pinned.`;

describe('UserMessage relay card', () => {
  it('renders a Guild relay as a card with sender, host, tag and time, not a bubble', () => {
    render(<UserMessage message={user(guild)} />);
    const card = screen.getByTestId('relay-message');
    expect(card).toHaveAttribute('data-relay-kind', 'guild');
    expect(card).toHaveTextContent('ticket-1339-catalog');
    expect(card).toHaveTextContent('Valhalla');
    expect(card).toHaveTextContent('via Guild');
    expect(card).toHaveTextContent('Two retired models are still pinned.');
    expect(card).not.toHaveTextContent('[Message from session');
    expect(screen.queryByTestId('user-message')).toBeNull();
    expect(card.querySelector('.niuu-chat-relay-time')).not.toBeNull();
  });

  it('opens the sender session from the header when the list knows it', async () => {
    const open = vi.fn();
    render(
      <SessionLinksProvider value={links(open)}>
        <UserMessage message={user(guild)} />
      </SessionLinksProvider>,
    );
    const header = screen.getByRole('link', { name: 'Open session ticket-1339-catalog' });
    expect(header).toHaveAttribute('href', catalog.href);
    await userEvent.click(header);
    expect(open).toHaveBeenCalledWith(UUID);
  });

  it('leaves a modified click to the browser', async () => {
    const open = vi.fn();
    render(
      <SessionLinksProvider value={links(open)}>
        <UserMessage message={user(guild)} />
      </SessionLinksProvider>,
    );
    const anchor = screen.getByRole('link', { name: /Open session/ });
    // jsdom cannot navigate; the browser's own handling of the click is not under test.
    anchor.addEventListener('click', (event) => event.preventDefault());
    fireEvent.click(anchor, { ctrlKey: true });
    expect(open).not.toHaveBeenCalled();
  });

  it('renders without a link when no page provides the session list', () => {
    render(<UserMessage message={user(guild)} />);
    expect(screen.queryByRole('link')).toBeNull();
    expect(screen.getByTestId('relay-message')).toHaveTextContent('ticket-1339-catalog');
  });

  it('renders a link-less card for a Guild sender the list does not know', () => {
    render(
      <SessionLinksProvider value={links()}>
        <UserMessage
          message={user('[Message from session ghost on Ymir, relayed through the Guild]\n\nHi')}
        />
      </SessionLinksProvider>,
    );
    expect(screen.getByTestId('relay-message')).toHaveTextContent('ghost');
    expect(screen.queryByRole('link')).toBeNull();
  });

  it('names an unnamed Guild sender by its resolved session', () => {
    render(
      <SessionLinksProvider value={links()}>
        <UserMessage
          message={user(`[Message from session ${UUID} on Ymir, relayed through the Guild]\n\nHi`)}
        />
      </SessionLinksProvider>,
    );
    expect(screen.getByTestId('relay-message')).toHaveTextContent('ticket-1339-catalog');
  });

  it('falls to "Another session" for an unnamed sender nobody can resolve', () => {
    render(
      <UserMessage
        message={user(`[Message from session ${UUID} on Ymir, relayed through the Guild]\n\nHi`)}
      />,
    );
    expect(screen.getByTestId('relay-message')).toHaveTextContent('Another session');
  });

  it('renders the web Forward as a card tagged forwarded once the sender is a known session', () => {
    render(
      <SessionLinksProvider value={links()}>
        <UserMessage message={user('Forwarded from ticket-1339-catalog:\n\nPlease check.')} />
      </SessionLinksProvider>,
    );
    const card = screen.getByTestId('relay-message');
    expect(card).toHaveAttribute('data-relay-kind', 'forwarded');
    expect(card).toHaveTextContent('forwarded');
    expect(card).toHaveTextContent('Please check.');
  });

  it('renders "From <name>:" as a card only when <name> is a known session', () => {
    const { rerender } = render(
      <SessionLinksProvider value={links()}>
        <UserMessage message={user('From ticket-1339-catalog (Valhalla):\n\nDone.')} />
      </SessionLinksProvider>,
    );
    const card = screen.getByTestId('relay-message');
    expect(card).toHaveAttribute('data-relay-kind', 'session');
    expect(card).toHaveTextContent('Valhalla');

    rerender(
      <SessionLinksProvider value={links()}>
        <UserMessage message={user('From Paris: it rained all week')} />
      </SessionLinksProvider>,
    );
    expect(screen.queryByTestId('relay-message')).toBeNull();
    expect(screen.getByTestId('user-message')).toHaveTextContent('From Paris: it rained all week');
  });

  it('keeps "From <name>:" the operator’s bubble when no page can confirm the sender', () => {
    render(<UserMessage message={user('From ticket-1339-catalog:\n\nDone.')} />);
    expect(screen.queryByTestId('relay-message')).toBeNull();
    expect(screen.getByTestId('user-message')).toBeInTheDocument();
  });

  it('keeps unrelated text and a turn this client sent as ordinary bubbles', () => {
    const { rerender } = render(<UserMessage message={user('Also link NIU-1342.')} />);
    expect(screen.getByTestId('user-message')).toBeInTheDocument();
    rerender(<UserMessage message={user(guild, { delivery: { state: 'delivered' } })} />);
    expect(screen.queryByTestId('relay-message')).toBeNull();
    expect(screen.getByTestId('user-message')).toBeInTheDocument();
  });

  it('collapses a long relay behind Show more and expands it', async () => {
    const body = Array.from({ length: 20 }, (_, i) => `line ${i}`).join('\n\n');
    render(
      <UserMessage
        message={user(`[Message from session coder on Ymir, relayed through the Guild]\n\n${body}`)}
      />,
    );
    const card = screen.getByTestId('relay-message');
    expect(card).toHaveAttribute('data-collapsed', 'true');
    const more = screen.getByRole('button', { name: 'Show more' });
    expect(more).toHaveAttribute('aria-expanded', 'false');
    await userEvent.click(more);
    expect(card).not.toHaveAttribute('data-collapsed');
    expect(screen.getByRole('button', { name: 'Show less' })).toHaveAttribute(
      'aria-expanded',
      'true',
    );
  });

  it('offers no Show more on a short relay', () => {
    render(<UserMessage message={user(guild)} />);
    expect(screen.queryByRole('button', { name: /Show/ })).toBeNull();
  });

  it('shows no time when timestamps are off', () => {
    localStorage.setItem('niuu.compactUx.timestamp', 'never');
    render(<UserMessage message={user(guild)} />);
    expect(screen.getByTestId('relay-message').querySelector('.niuu-chat-relay-time')).toBeNull();
  });
});
