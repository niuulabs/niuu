import type {
  CatalogEntry,
  ConnectIntegrationInput,
  IntegrationConnection,
  IntegrationTestResult,
  SetupState,
  SystemReport,
} from './domain/setup';

/**
 * Everything the wizard needs from the platform.
 *
 * Progress and host facts come from the setup API; providers, git and
 * trackers are connected through the existing integrations API so the
 * wizard never becomes a second configuration system.
 */
export interface ISetupService {
  getState(): Promise<SetupState>;
  getSystem(): Promise<SystemReport>;
  completeStep(step: string, data?: Record<string, unknown>): Promise<SetupState>;
  complete(): Promise<SetupState>;
  listCatalog(): Promise<CatalogEntry[]>;
  listIntegrations(): Promise<IntegrationConnection[]>;
  connectIntegration(input: ConnectIntegrationInput): Promise<IntegrationConnection>;
  testIntegration(connectionId: string): Promise<IntegrationTestResult>;
}
