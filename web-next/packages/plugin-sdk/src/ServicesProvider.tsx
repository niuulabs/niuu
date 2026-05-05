import { createContext, useContext, type ReactNode } from 'react';

export type ServicesMap = Record<string, unknown>;

const ServicesContext = createContext<ServicesMap | null>(null);

interface ServicesProviderProps {
  services: ServicesMap;
  children: ReactNode;
}

export function ServicesProvider({ services, children }: ServicesProviderProps) {
  return <ServicesContext.Provider value={services}>{children}</ServicesContext.Provider>;
}

export function useService<T>(key: string): T {
  const services = useContext(ServicesContext);
  if (!services) {
    throw new Error('useService must be used within <ServicesProvider>');
  }
  const service = services[key];
  if (service === undefined) {
    throw new Error(`Service "${key}" was not registered in ServicesProvider`);
  }
  return service as T;
}
