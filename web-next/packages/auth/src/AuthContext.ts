import { createContext } from 'react';
import type { User } from 'oidc-client-ts';

export interface AuthContextValue {
  /** Whether auth is enabled (OIDC configured) */
  enabled: boolean;
  /** Whether the user is authenticated */
  authenticated: boolean;
  /** Whether auth state is still loading */
  loading: boolean;
  /** The OIDC user object (null if not authenticated or auth disabled) */
  user: User | null;
  /** The access token (null if not authenticated or auth disabled) */
  accessToken: string | null;
  /** Trigger OIDC login redirect */
  login: () => void;
  /** Trigger OIDC logout */
  logout: () => void;
}

export const AuthContext = createContext<AuthContextValue>({
  enabled: false,
  authenticated: false,
  loading: true,
  user: null,
  accessToken: null,
  login: () => {},
  logout: () => {},
});
