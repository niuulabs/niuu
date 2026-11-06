import { createContext, useContext } from 'react';
import type { SecretFulfillment, SecretRequest } from '@niuulabs/domain';

/**
 * What a secret request card needs from its host, bound to the current session. The value
 * goes to `fulfill` and nowhere else: the port must send it to Forge with the person's own
 * credentials and must not cache, log or return it.
 */
export interface SecretRequestsPort {
  get(requestId: string): Promise<SecretRequest>;
  fulfill(requestId: string, fulfillment: SecretFulfillment): Promise<SecretRequest>;
  decline(requestId: string): Promise<SecretRequest>;
}

/** The host supplies it; without one a request card is read-only. */
export const SecretRequestsContext = createContext<SecretRequestsPort | undefined>(undefined);

export function useSecretRequests(): SecretRequestsPort | undefined {
  return useContext(SecretRequestsContext);
}
