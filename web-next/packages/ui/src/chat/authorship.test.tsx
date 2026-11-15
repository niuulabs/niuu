import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import {
  ChatAuthorshipProvider,
  authorInitials,
  authorName,
  conversationAuthorship,
  showsAuthor,
  useChatAuthorship,
} from './authorship';
import type { ChatMessage, MessageAuthor } from './types';

const alice: MessageAuthor = { userId: 'u-alice', displayName: 'Alice Liddell' };
const bob: MessageAuthor = { userId: 'u-bob', displayName: 'Bob Stone' };

function user(id: string, author?: MessageAuthor): ChatMessage {
  return { id, role: 'user', content: id, createdAt: new Date(0), ...(author ? { author } : {}) };
}

describe('conversationAuthorship', () => {
  it('is single-author when one person sent every message', () => {
    const messages = [user('1', alice), user('2', alice)];
    expect(conversationAuthorship(messages, 'u-alice')).toEqual({
      viewerId: 'u-alice',
      multipleAuthors: false,
    });
  });

  it('is multi-author once two different people have spoken', () => {
    const messages = [user('1', alice), user('2', bob)];
    expect(conversationAuthorship(messages).multipleAuthors).toBe(true);
  });

  it('counts only user messages that have an author', () => {
    const assistant: ChatMessage = {
      id: 'a',
      role: 'assistant',
      content: 'hi',
      createdAt: new Date(0),
      author: bob,
    };
    const messages = [user('1', alice), user('2'), assistant];
    expect(conversationAuthorship(messages).multipleAuthors).toBe(false);
  });
});

describe('showsAuthor', () => {
  const solo = { viewerId: 'u-alice', multipleAuthors: false };

  it('keeps the plain look for the viewer in a single-person session', () => {
    expect(showsAuthor(user('1', alice), solo)).toBe(false);
  });

  it('names someone other than the viewer even in a single-author conversation', () => {
    expect(showsAuthor(user('1', bob), solo)).toBe(true);
  });

  it('names every author, the viewer included, once several people have spoken', () => {
    const several = { viewerId: 'u-alice', multipleAuthors: true };
    expect(showsAuthor(user('1', alice), several)).toBe(true);
    expect(showsAuthor(user('2', bob), several)).toBe(true);
  });

  it('cannot tell who the viewer is, so a lone author keeps the plain look', () => {
    expect(showsAuthor(user('1', bob), { multipleAuthors: false })).toBe(false);
  });

  it('names nobody on a message with no recorded author, whatever the conversation', () => {
    expect(showsAuthor(user('1'), { viewerId: 'u-alice', multipleAuthors: true })).toBe(false);
  });

  it('never names the author of an assistant message', () => {
    const assistant: ChatMessage = {
      id: 'a',
      role: 'assistant',
      content: 'hi',
      createdAt: new Date(0),
      author: bob,
    };
    expect(showsAuthor(assistant, { viewerId: 'u-alice', multipleAuthors: true })).toBe(false);
  });
});

describe('authorName and authorInitials', () => {
  it('uses the display name', () => {
    expect(authorName(alice)).toBe('Alice Liddell');
    expect(authorInitials(alice)).toBe('AL');
  });

  it('names a person with no display name by their id', () => {
    const nameless = { userId: 'u-carol', displayName: '' };
    expect(authorName(nameless)).toBe('u-carol');
    expect(authorInitials(nameless)).toBe('UC');
  });

  it('reads an email by its local part', () => {
    expect(authorInitials({ userId: 'u', displayName: 'ada.lovelace@example.test' })).toBe('AL');
  });

  it('takes one initial from a one-word name and handles non-ASCII letters', () => {
    expect(authorInitials({ userId: 'u', displayName: 'madonna' })).toBe('M');
    expect(authorInitials({ userId: 'u', displayName: 'élodie Ñandú' })).toBe('ÉÑ');
  });

  it('has no initials for a name with no letters', () => {
    expect(authorInitials({ userId: '--', displayName: '' })).toBe('');
  });
});

describe('ChatAuthorshipProvider', () => {
  function Probe() {
    const authorship = useChatAuthorship();
    return <span data-testid="probe">{JSON.stringify(authorship)}</span>;
  }

  it('is a single-person conversation without a provider', () => {
    render(<Probe />);
    expect(screen.getByTestId('probe').textContent).toBe('{"multipleAuthors":false}');
  });

  it('provides the conversation authorship to its messages', () => {
    render(
      <ChatAuthorshipProvider value={{ viewerId: 'u-bob', multipleAuthors: true }}>
        <Probe />
      </ChatAuthorshipProvider>,
    );
    expect(JSON.parse(screen.getByTestId('probe').textContent ?? '')).toEqual({
      viewerId: 'u-bob',
      multipleAuthors: true,
    });
  });
});
