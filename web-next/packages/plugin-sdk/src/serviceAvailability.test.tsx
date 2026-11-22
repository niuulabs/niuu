import { describe, expect, it } from 'vitest';
import { renderHook } from '@testing-library/react';
import type { ReactNode } from 'react';
import { ServicesProvider } from './ServicesProvider';
import {
  UNAVAILABLE_SERVICE,
  isUnavailableService,
  useServiceAvailable,
} from './serviceAvailability';

const absent = { [UNAVAILABLE_SERVICE]: { available: false, serviceName: 'ting', reason: 'off' } };

function available(key: string, services: Record<string, unknown>) {
  const wrapper = ({ children }: { children: ReactNode }) => (
    <ServicesProvider services={services}>{children}</ServicesProvider>
  );
  return renderHook(() => useServiceAvailable(key), { wrapper }).result.current;
}

describe('service availability', () => {
  it('tells a service without a backend from one that is configured', () => {
    expect(isUnavailableService(absent)).toBe(true);
    expect(isUnavailableService({ getSagas: () => [] })).toBe(false);
    expect(isUnavailableService(undefined)).toBe(false);
    expect(isUnavailableService({ [UNAVAILABLE_SERVICE]: undefined })).toBe(false);
  });

  it('asks the configuration only, so a configured service that fails is available', () => {
    const failing = {
      getSagas: () => {
        throw new Error('API request failed: 404');
      },
    };
    expect(available('ting', { ting: failing })).toBe(true);
    expect(available('ting', { ting: absent })).toBe(false);
    expect(available('ting', {})).toBe(false);
  });

  it('has no services outside a provider', () => {
    expect(renderHook(() => useServiceAvailable('ting')).result.current).toBe(false);
  });
});
