import type { IntegrationTestResult } from '../domain/setup';
import { AlertIcon, CheckIcon } from './icons';

export interface TestOutcomeProps {
  slug: string;
  result: IntegrationTestResult;
}

/** Maximum repositories named inline; the rest is a count. */
export const REPOSITORIES_SHOWN = 5;

/** What a connection test proved: who you are, what you can reach, or why not. */
export function TestOutcome({ slug, result }: TestOutcomeProps) {
  if (!result.success) {
    return (
      <span className="setup-note setup-note--warn" data-testid={`setup-test-failed-${slug}`}>
        <AlertIcon size={13} /> {result.error ?? 'Test failed'}
      </span>
    );
  }
  const shown = result.repositories.slice(0, REPOSITORIES_SHOWN);
  const rest = result.repositories.length - shown.length;
  return (
    <span className="setup-note" data-testid={`setup-test-ok-${slug}`}>
      <CheckIcon size={13} /> {result.detail ?? 'Works'}
      {result.workspace ? ` · ${result.workspace}` : ''}
      {result.user ? ` · ${result.user}` : ''}
      {shown.length > 0 ? (
        <span data-testid={`setup-test-repos-${slug}`}>
          {' '}
          · {shown.join(', ')}
          {rest > 0 ? ` and ${rest} more` : ''}
        </span>
      ) : null}
    </span>
  );
}
