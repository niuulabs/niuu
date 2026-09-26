/** "Right now" feed line — "<actor> is reading <title>" / "<actor> wrote to <title>". */
import type { LiveActivity } from './api-types';

export function describeLiveActivity(
  entry: Pick<LiveActivity, 'actor' | 'kind'>,
  pageTitle: string,
): string {
  const actor = entry.actor ?? 'Someone';
  return entry.kind === 'read'
    ? `${actor} is reading ${pageTitle}`
    : `${actor} wrote to ${pageTitle}`;
}
