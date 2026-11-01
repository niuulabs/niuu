/**
 * The words the home page and the realms home put around their numbers.
 * Small counts read better spelled out; anything larger stays a numeral.
 */
const WORDS = ['No', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight', 'Nine', 'Ten'];

export function countWords(count: number): string {
  return WORDS[count] ?? String(count);
}

/** The greeting, with the signed-in first name when the identity service gives one. */
export function greeting(displayName: string | null | undefined): string {
  const first = (displayName ?? '').trim().split(/\s+/)[0] ?? '';
  return first ? `Good morning, ${first}.` : 'Good morning.';
}

export interface HomeState {
  realms: number;
  running: number;
  needsYou: number;
}

/** One sentence of where things stand, above the three ways to start. */
export function stateSentence({ realms, running, needsYou }: HomeState): string {
  const parts: string[] = [];
  if (realms > 0) parts.push(`${countWords(realms).toLowerCase()} realm${realms === 1 ? '' : 's'}`);
  if (running > 0) {
    parts.push(`${countWords(running).toLowerCase()} session${running === 1 ? '' : 's'} running`);
  }
  if (needsYou > 0) {
    parts.push(
      `${countWords(needsYou).toLowerCase()} ${needsYou === 1 ? 'thing needs' : 'things need'} you`,
    );
  }
  if (parts.length === 0) return 'Nothing is running yet. Pick one of the three below.';
  const sentence = parts.join(', ');
  return `${sentence.charAt(0).toUpperCase()}${sentence.slice(1)}.`;
}
