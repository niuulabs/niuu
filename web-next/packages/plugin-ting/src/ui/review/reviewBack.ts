import { useRouterState } from '@tanstack/react-router';

/** Where the review page sends a person back to, and what it calls that place. */
export interface ReviewBack {
  pathname: string;
  search: Record<string, string>;
  label: string;
}

/** The page a person is on, to hand to a review link as `returnTo`. */
export function useReturnTo(): string {
  return useRouterState({ select: (state) => state.location.href });
}

function backLabel(pathname: string, search: URLSearchParams): string {
  if (pathname.startsWith('/realms/projects')) return 'Back to the project';
  if (pathname.startsWith('/realms/') && search.get('tab') === 'board') return 'Back to the board';
  if (pathname.startsWith('/realms/')) return 'Back to the workspace';
  if (pathname.startsWith('/realms')) return 'Back to Projects';
  if (pathname.startsWith('/ting/artifacts')) return 'Back to Artifacts';
  if (pathname.startsWith('/ting/work/') && search.get('view') === 'board') {
    return 'Back to the board';
  }
  if (pathname.startsWith('/ting/work')) return 'Back to Work';
  if (pathname.startsWith('/volundr')) return 'Back to Forge';
  return 'Back';
}

/**
 * The place a review was opened from, read from its `returnTo`. Only a path inside this app
 * is followed; anything else, or nothing, goes back to the page's own home.
 */
export function reviewBack(returnTo: string | undefined, home: ReviewBack): ReviewBack {
  if (!returnTo || !returnTo.startsWith('/') || returnTo.startsWith('//')) return home;
  const [pathname = '/', query = ''] = returnTo.split('?');
  const search = new URLSearchParams(query);
  return {
    pathname,
    search: Object.fromEntries(search.entries()),
    label: backLabel(pathname, search),
  };
}
