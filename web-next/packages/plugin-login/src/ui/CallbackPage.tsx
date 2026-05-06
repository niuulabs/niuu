import { useEffect } from 'react';
import { useAuth } from '@niuulabs/auth';
import './CallbackPage.css';

/**
 * OIDC callback landing page.
 *
 * AuthProvider automatically processes the `?code=…` or `?error=…` query
 * params on any route. This page simply shows a loading spinner while that
 * processing is in flight, then redirects to the app root once the user is
 * authenticated.
 *
 * Uses `position: fixed` to overlay the Shell when rendered inside it.
 */
export function CallbackPage() {
  const { loading, authenticated, enabled } = useAuth();

  useEffect(() => {
    if (!loading && enabled && authenticated) {
      window.location.replace('/');
    }
  }, [loading, enabled, authenticated]);

  return (
    <div className="callback-page" data-testid="callback-page">
      <span className="callback-page__spinner" aria-label="Completing sign in…" />
      <p className="callback-page__label">Completing sign in…</p>
    </div>
  );
}
