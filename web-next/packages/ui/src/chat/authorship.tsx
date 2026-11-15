import { createContext, useContext, type ReactNode } from 'react';
import type { ChatMessage, MessageAuthor } from './types';

/**
 * Who is speaking in the conversation, for the page that shows it. A session can have more
 * than one person in it; each message they send carries its author.
 */
export interface ChatAuthorship {
  /** The signed-in person looking at the conversation, when the page knows who that is. */
  viewerId?: string;
  /** More than one person has spoken in this conversation. */
  multipleAuthors: boolean;
}

const SOLO: ChatAuthorship = { multipleAuthors: false };

const ChatAuthorshipContext = createContext<ChatAuthorship>(SOLO);

export interface ChatAuthorshipProviderProps {
  value: ChatAuthorship;
  children: ReactNode;
}

/**
 * The conversation provides this around its messages so a user message can tell whether to
 * name its author. Without a provider every conversation is a single person's.
 */
export function ChatAuthorshipProvider({ value, children }: ChatAuthorshipProviderProps) {
  return <ChatAuthorshipContext.Provider value={value}>{children}</ChatAuthorshipContext.Provider>;
}

export function useChatAuthorship(): ChatAuthorship {
  return useContext(ChatAuthorshipContext);
}

/** The authorship of a conversation: how many different people sent its user messages. */
export function conversationAuthorship(
  messages: readonly ChatMessage[],
  viewerId?: string,
): ChatAuthorship {
  const authors = new Set<string>();
  for (const message of messages) {
    if (message.role === 'user' && message.author) authors.add(message.author.userId);
  }
  return { viewerId, multipleAuthors: authors.size > 1 };
}

/**
 * Whether a user message names its author: when someone other than the viewer wrote it, or
 * when the conversation has several people in it (the viewer's own messages are named too,
 * so every voice reads alike). A message with no recorded author (one from before authorship
 * existed) names nobody, and a single-person conversation keeps its plain look.
 */
export function showsAuthor(message: ChatMessage, authorship: ChatAuthorship): boolean {
  if (message.role !== 'user' || !message.author) return false;
  if (authorship.multipleAuthors) return true;
  return authorship.viewerId !== undefined && message.author.userId !== authorship.viewerId;
}

/** The name an author goes by: the display name, else the id the identity provider knows. */
export function authorName(author: MessageAuthor): string {
  return author.displayName || author.userId;
}

/**
 * The avatar's initials: the first letters of the first two words of the name. An email
 * (the name of a person with no display name) is read by its part before the `@`.
 */
export function authorInitials(author: MessageAuthor): string {
  const name = authorName(author).split('@')[0] ?? '';
  const words = name.split(/[^\p{L}\p{N}]+/u).filter(Boolean);
  const initials = words
    .slice(0, 2)
    .map((word) => Array.from(word)[0] ?? '')
    .join('');
  return initials.toUpperCase();
}
