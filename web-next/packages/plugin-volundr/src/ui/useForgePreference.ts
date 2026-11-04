import { useStoredPreference } from '@niuulabs/ui';

/** Preferences are optional browser-local presentation state, never session state. */
export function useForgePreference<T extends string>(
  name: string,
  initial: T,
  allowed?: readonly T[],
): readonly [T, (value: T) => void] {
  return useStoredPreference(`niuu.forge.${name}`, initial, allowed);
}
