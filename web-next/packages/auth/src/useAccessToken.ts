import { useAuth } from './useAuth';

export function useAccessToken(): string | null {
  return useAuth().accessToken;
}
