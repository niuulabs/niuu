import type { User } from 'oidc-client-ts';
import { useAuth } from './useAuth';

export function useUser(): User | null {
  return useAuth().user;
}
