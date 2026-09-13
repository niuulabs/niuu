import { Fragment, type ReactNode } from 'react';

/**
 * Absolute in-app paths (`/settings/runtime/sessions`) and http(s) URLs inside a
 * message. A trailing sentence character is left out of the link.
 */
const LINK_RE = /(https?:\/\/[^\s)]+|(?<![\w/])\/[a-z][\w\-./]*(?:\?[\w=&\-.%]+)?)/gi;
const TRAILING_RE = /[.,;:!?]+$/;

/** The links a message points at, in order; empty when it has none. */
export function extractLinks(text: string): string[] {
  const links: string[] = [];
  for (const match of text.matchAll(LINK_RE)) {
    links.push(match[0].replace(TRAILING_RE, ''));
  }
  return links;
}

const LINK_CLASS = 'niuu:underline niuu:underline-offset-2 niuu:hover:text-text-primary';

export interface LinkedTextProps {
  text: string;
  className?: string;
}

/**
 * A message with its in-app paths and URLs rendered as links, so an error
 * that says where to fix something ("raise the limit in Settings → Runtime
 * (/settings/runtime/sessions)") can be followed with a click.
 */
export function LinkedText({ text, className }: LinkedTextProps) {
  const parts: ReactNode[] = [];
  let last = 0;
  for (const match of text.matchAll(LINK_RE)) {
    const start = match.index ?? 0;
    const raw = match[0];
    const href = raw.replace(TRAILING_RE, '');
    const trailing = raw.slice(href.length);
    if (start > last) parts.push(text.slice(last, start));
    parts.push(
      <a key={`${start}-${href}`} href={href} className={LINK_CLASS}>
        {href}
      </a>,
    );
    if (trailing) parts.push(trailing);
    last = start + raw.length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return (
    <span className={className}>
      {parts.map((part, index) => (
        <Fragment key={index}>{part}</Fragment>
      ))}
    </span>
  );
}
