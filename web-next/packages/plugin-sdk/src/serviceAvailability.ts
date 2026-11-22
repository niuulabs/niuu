import { useContext } from 'react';
import { ServicesContext } from './ServicesProvider';

/** Why a registered service cannot be called: no backend is configured for it. */
export interface UnavailableServiceStatus {
  available: false;
  serviceName: string;
  reason: string;
}

/** Marks a registered stand-in for a service no backend is configured for. */
export const UNAVAILABLE_SERVICE = Symbol.for('niuu.unavailable-service');

export function isUnavailableService(
  service: unknown,
): service is { [UNAVAILABLE_SERVICE]: UnavailableServiceStatus } {
  return Boolean(
    service &&
    typeof service === 'object' &&
    UNAVAILABLE_SERVICE in service &&
    (service as { [UNAVAILABLE_SERVICE]?: UnavailableServiceStatus })[UNAVAILABLE_SERVICE]
      ?.available === false,
  );
}

/**
 * Whether a backend is configured for a service. It answers from the runtime config
 * alone: a configured backend that fails is available, and its error is shown.
 */
export function useServiceAvailable(key: string): boolean {
  const service = useContext(ServicesContext)?.[key];
  return service !== undefined && !isUnavailableService(service);
}
