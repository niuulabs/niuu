import { useQuery } from '@tanstack/react-query';
import type { ParticipantCandidate } from '../models/volundr.model';
import { errorText } from './errorText';

export const CONTROL =
  'niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-tertiary niuu:px-2 niuu:py-1 niuu:text-sm niuu:text-text-primary';
const ERROR = 'niuu:text-xs niuu:text-critical-fg';

/** What a person is called in a list: their name, else their email, else their id. */
export function nameOf(person: { displayName: string; userId: string; email?: string }): string {
  return person.displayName || person.email || person.userId;
}

/**
 * Find people of the org to invite: a text box and the matches under it. Where the matches
 * come from is the caller's `fetcher`, so the same picker serves a session that exists (its
 * own candidates) and a team that does not exist yet (the Forge's). The caller owns the text
 * and the pick; `children` sit beside the box, for what the caller does with a pick.
 */
export function CandidatePicker({
  queryKey,
  fetcher,
  query,
  onQueryChange,
  chosen,
  onChoose,
  exclude,
  children,
}: {
  /** Where matches are cached; the typed text is added to it. */
  queryKey: readonly unknown[];
  fetcher: (query: string, signal?: AbortSignal) => Promise<ParticipantCandidate[]>;
  query: string;
  onQueryChange: (query: string) => void;
  /** The user id picked from the matches, when the text is that pick. */
  chosen: string | null;
  onChoose: (person: ParticipantCandidate) => void;
  /** People already taken, left out of the matches. */
  exclude?: ReadonlySet<string>;
  children?: React.ReactNode;
}) {
  const typed = query.trim();
  const candidates = useQuery({
    queryKey: [...queryKey, typed],
    queryFn: ({ signal }) => fetcher(typed, signal),
    enabled: typed.length > 0 && chosen === null,
  });
  const matches = (candidates.data ?? []).filter((person) => !exclude?.has(person.userId));

  return (
    <>
      <div className="niuu:flex niuu:gap-2">
        <input
          aria-label="Find someone in your org"
          className={`${CONTROL} niuu:min-w-0 niuu:flex-1`}
          placeholder="Name or email"
          value={query}
          onChange={(event) => onQueryChange(event.target.value)}
        />
        {children}
      </div>
      {typed && !chosen ? (
        <ul className="niuu:m-0 niuu:flex niuu:list-none niuu:flex-col niuu:gap-0.5 niuu:p-0">
          {matches.map((person) => (
            <li key={person.userId}>
              <button
                type="button"
                className="niuu:w-full niuu:rounded-md niuu:px-2 niuu:py-1 niuu:text-left niuu:text-sm niuu:text-text-primary niuu:hover:bg-bg-tertiary"
                onClick={() => onChoose(person)}
              >
                {nameOf(person)}
                {person.email && person.displayName ? (
                  <span className="niuu:ml-2 niuu:text-xs niuu:text-text-muted">
                    {person.email}
                  </span>
                ) : null}
              </button>
            </li>
          ))}
          {candidates.isSuccess && matches.length === 0 ? (
            <li className="niuu:px-2 niuu:text-xs niuu:text-text-muted">
              Nobody else in your org matches.
            </li>
          ) : null}
          {candidates.error ? <li className={ERROR}>{errorText(candidates.error)}</li> : null}
        </ul>
      ) : null}
    </>
  );
}
