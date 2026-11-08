/**
 * A user turn that another session wrote, not the operator. Sessions write user turns into
 * each other (a coordinator steering a worker, a worker reporting back, the web's Forward),
 * so the transcript names the sender instead of showing it as the operator's own bubble.
 *
 * The rules follow Lexi's classifier: a header on the FIRST line decides, and only a
 * header the platform itself writes counts. Anything else is the operator's bubble.
 */

export type RelayKind = 'guild' | 'forwarded' | 'session';

export interface RelayedTurn {
  kind: RelayKind;
  /** The capsule beside the sender: how the turn reached this session. */
  tag: string;
  /** The sender's name; null when the header carries only its session id. */
  senderName: string | null;
  /** The sender's session id, when the header carries it. */
  senderSessionId: string | null;
  /** The sender's host, when the header names one. */
  host: string | null;
  /** The Markdown under the header; the header line is consumed. */
  body: string;
  /**
   * The header is plain text any person could type ("From Paris: …"). It is a relay only
   * when the sender is a session the page knows; the platform's own headers need no check.
   */
  needsKnownSender: boolean;
}

export const RELAY_TAGS: Record<RelayKind, string> = {
  guild: 'via Guild',
  forwarded: 'forwarded',
  session: 'session',
};

/**
 * Past this many non-empty lines, or this many characters (a few long paragraphs wrap past
 * the line limit), the card starts collapsed behind Show more. The same limits as Lexi.
 */
export const RELAY_COLLAPSE_LINES = 12;
export const RELAY_COLLAPSE_CHARACTERS = 1200;

const GUILD_PREFIX = '[Message from session ';
const GUILD_SUFFIX = ', relayed through the Guild]';
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
// A session name as Forge allows it: letters, digits, dot, underscore, hyphen.
const NAME = '[A-Za-z0-9._-]+';
const FROM_HEADER = new RegExp(`^From (${NAME})(?: \\(([^()]+)\\))?(?:[:,.](?:\\s|$)| — )`);
const FORWARDED_HEADER = new RegExp(`^Forwarded from (${NAME}):(?:\\s|$)`);

function splitFirstLine(text: string): { line: string; rest: string } {
  const newline = text.indexOf('\n');
  if (newline < 0) return { line: text, rest: '' };
  return { line: text.slice(0, newline), rest: text.slice(newline + 1) };
}

/** The text from its first line that has any text; leading blank lines are dropped. */
function withoutLeadingBlankLines(text: string): string {
  return text.replace(/^(?:[ \t]*\r?\n)+/, '');
}

function guildHeader(line: string): Omit<RelayedTurn, 'body' | 'tag' | 'kind'> | null {
  const header = line.trimEnd();
  if (!header.startsWith(GUILD_PREFIX) || !header.endsWith(GUILD_SUFFIX)) return null;
  const inner = header.slice(GUILD_PREFIX.length, header.length - GUILD_SUFFIX.length);
  const on = inner.lastIndexOf(' on ');
  if (on < 0) return null;
  const who = inner.slice(0, on);
  const host = inner.slice(on + ' on '.length);
  if (!who || !host) return null;
  const named = /^(.+) \(([0-9a-f-]{36})\)$/i.exec(who);
  if (named && named[1] && named[2] && UUID.test(named[2])) {
    return {
      senderName: named[1],
      senderSessionId: named[2].toLowerCase(),
      host,
      needsKnownSender: false,
    };
  }
  if (UUID.test(who)) {
    return { senderName: null, senderSessionId: who.toLowerCase(), host, needsKnownSender: false };
  }
  return { senderName: who, senderSessionId: null, host, needsKnownSender: false };
}

/**
 * The relay a user turn is, or null for the operator's own turn. Header forms:
 * the Guild relay (`[Message from session <name> (<uuid>) on <host>, relayed through the
 * Guild]`), the web's `Forwarded from <name>:`, and a session's `From <name>:` or
 * `From <name> (<host>):`.
 */
export function classifyRelayedTurn(text: string): RelayedTurn | null {
  const lead = text.trimStart();
  const { line, rest } = splitFirstLine(lead);

  const guild = guildHeader(line);
  if (guild) {
    return {
      ...guild,
      kind: 'guild',
      tag: RELAY_TAGS.guild,
      body: withoutLeadingBlankLines(rest).trimEnd(),
    };
  }

  const forwarded = FORWARDED_HEADER.exec(line);
  if (forwarded?.[1]) {
    return {
      kind: 'forwarded',
      tag: RELAY_TAGS.forwarded,
      senderName: forwarded[1],
      senderSessionId: null,
      host: null,
      body: withoutLeadingBlankLines(rest).trimEnd(),
      // The platform's Forward writes this line, but so can anyone: confirm the sender.
      needsKnownSender: true,
    };
  }

  const from = FROM_HEADER.exec(line);
  if (from?.[1]) {
    const sameLine = line.slice(from[0].length).trim();
    const below = withoutLeadingBlankLines(rest).trimEnd();
    return {
      kind: 'session',
      tag: RELAY_TAGS.session,
      senderName: from[1],
      senderSessionId: null,
      host: from[2] ?? null,
      body: sameLine && below ? `${sameLine}\n${below}` : sameLine || below,
      needsKnownSender: true,
    };
  }

  return null;
}

/** More than the line or the character limit; counted once, from the source text. */
export function isLongRelay(body: string): boolean {
  if (body.length > RELAY_COLLAPSE_CHARACTERS) return true;
  const lines = body.split('\n').filter((line) => line.trim().length > 0);
  return lines.length > RELAY_COLLAPSE_LINES;
}
