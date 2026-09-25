/**
 * Starting a ravn as a Forge session.
 *
 * A Forge flock session runs the persona's ravn daemon and a Skuld room as
 * processes on the Forge host — no container and no resident profile needed.
 * Forge names sessions as RFC 1123 labels, so the name people type is turned
 * into one here, and refused when nothing usable is left.
 */

/** Forge's limit on a session name (an RFC 1123 label). */
export const SESSION_NAME_MAX = 63;

export function sessionNameFor(name: string): string {
  return name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9-]+/g, '-')
    .replace(/-{2,}/g, '-')
    .slice(0, SESSION_NAME_MAX)
    .replace(/^-+|-+$/g, '');
}

export interface RavnSessionLaunch {
  name: string;
  persona: string;
}

/**
 * The Forge session request for a one-persona ravn flock with its own fresh
 * workspace. The flock's model comes from the Forge's ravn configuration, so
 * the request names none.
 */
export function ravnSessionRequest(launch: RavnSessionLaunch) {
  return {
    name: sessionNameFor(launch.name),
    model: '',
    // An empty git source gives the session its own new workspace directory.
    source: { type: 'git' as const, repo: '', branch: 'main' },
    workloadType: 'ravn_flock' as const,
    workloadConfig: { personas: [launch.persona] },
  };
}
