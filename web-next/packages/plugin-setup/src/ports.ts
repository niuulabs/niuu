import type {
  ApplyStatus,
  CatalogEntry,
  ConnectIntegrationInput,
  Enrollment,
  IntegrationConnection,
  StackChanges,
  StackView,
  IntegrationTestResult,
  OAuthClientInput,
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
  /** Start (or resume) an interactive provider sign-in for a catalog entry. */
  startEnrollment(slug: string, credentialName: string): Promise<Enrollment>;
  /** Current state of a sign-in; the backend polls the login helper on each read. */
  getEnrollment(enrollmentId: string): Promise<Enrollment>;
  cancelEnrollment(enrollmentId: string): Promise<Enrollment>;
  /** Hand a browser authorization code back to a sign-in that asked for one. */
  submitEnrollmentCode(enrollmentId: string, code: string): Promise<Enrollment>;
  /** Register the person's own OAuth application for a provider's sign-in (GitHub, GitLab). */
  registerOAuthClient(slug: string, input: OAuthClientInput): Promise<void>;
  /** Bundle settings the wizard may change (docker mode); rejects when unavailable. */
  getStack(): Promise<StackView>;
  stageStack(changes: StackChanges): Promise<StackView>;
  discardStack(): Promise<StackView>;
  /** Re-render the bundle with the staged changes and restart what changed. */
  applyStack(): Promise<ApplyStatus>;
  stackStatus(): Promise<ApplyStatus>;
}
