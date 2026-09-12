/**
 * HTTP adapter for the setup wizard.
 *
 * Two clients: the setup API (`/api/v1/niuu/setup`) for progress and host
 * facts, and the integrations API (`/api/v1/integrations`) for connecting
 * providers, git and trackers. Both are structurally compatible with
 * `createApiClient(basePath)` from @niuulabs/query.
 */

import type {
  CatalogEntry,
  CatalogSchema,
  ConnectIntegrationInput,
  Enrollment,
  EnrollmentState,
  IntegrationConnection,
  IntegrationTestResult,
  SetupState,
  SystemReport,
} from '../domain/setup';
import type { ISetupService } from '../ports';

export interface HttpClient {
  get<T>(endpoint: string): Promise<T>;
  post<T>(endpoint: string, body?: unknown): Promise<T>;
  put<T>(endpoint: string, body: unknown): Promise<T>;
  delete<T>(endpoint: string): Promise<T>;
}

export interface SetupHttpClients {
  setup: HttpClient;
  integrations: HttpClient;
}

/** Wire shape of `GET /api/v1/integrations/catalog` entries (snake_case). */
export interface CatalogEntryWire {
  slug: string;
  name: string;
  description: string;
  integration_type: string;
  auth_type?: string;
  credential_schema?: CatalogSchema;
  config_schema?: CatalogSchema;
}

/** Wire shape of `GET /api/v1/integrations` rows (snake_case). */
export interface IntegrationWire {
  id: string;
  slug?: string;
  integration_type: string;
  credential_name: string;
  enabled: boolean;
  config?: Record<string, unknown>;
  credential_status?: string;
}

export interface IntegrationTestWire {
  success: boolean;
  provider: string;
  workspace?: string | null;
  user?: string | null;
  error?: string | null;
}

/** Wire shape of `/api/v1/integrations/enrollments` responses (camelCase aliases). */
export interface EnrollmentWire {
  id: string;
  connectionId: string;
  providerSlug: string;
  credentialName: string;
  state: string;
  verificationUri?: string;
  userCode?: string;
  expiresAt: string;
  errorCode?: string;
  inputRequired?: boolean;
}

export function mapEnrollment(row: EnrollmentWire): Enrollment {
  return {
    id: row.id,
    connectionId: row.connectionId,
    providerSlug: row.providerSlug,
    credentialName: row.credentialName,
    state: row.state as EnrollmentState,
    verificationUri: row.verificationUri ?? '',
    userCode: row.userCode ?? '',
    expiresAt: row.expiresAt,
    errorCode: row.errorCode ?? '',
    inputRequired: row.inputRequired ?? false,
  };
}

export function mapCatalogEntry(entry: CatalogEntryWire): CatalogEntry {
  return {
    slug: entry.slug,
    name: entry.name,
    description: entry.description,
    integrationType: entry.integration_type,
    authType: entry.auth_type ?? 'api_key',
    credentialSchema: entry.credential_schema ?? {},
    configSchema: entry.config_schema ?? {},
  };
}

export function mapIntegration(row: IntegrationWire): IntegrationConnection {
  return {
    id: row.id,
    slug: row.slug ?? '',
    integrationType: row.integration_type,
    credentialName: row.credential_name,
    enabled: row.enabled,
    config: row.config ?? {},
    credentialStatus: row.credential_status ?? 'unknown',
  };
}

export function mapTestResult(result: IntegrationTestWire): IntegrationTestResult {
  return {
    success: result.success,
    provider: result.provider,
    workspace: result.workspace ?? null,
    user: result.user ?? null,
    error: result.error ?? null,
  };
}

export function buildSetupHttpAdapter(clients: SetupHttpClients): ISetupService {
  return {
    getState(): Promise<SetupState> {
      return clients.setup.get<SetupState>('');
    },
    getSystem(): Promise<SystemReport> {
      return clients.setup.get<SystemReport>('/system');
    },
    completeStep(step: string, data: Record<string, unknown> = {}): Promise<SetupState> {
      return clients.setup.put<SetupState>(`/steps/${encodeURIComponent(step)}`, { data });
    },
    complete(): Promise<SetupState> {
      return clients.setup.post<SetupState>('/complete');
    },
    async listCatalog(): Promise<CatalogEntry[]> {
      const rows = await clients.integrations.get<CatalogEntryWire[]>('/catalog');
      return rows.map(mapCatalogEntry);
    },
    async listIntegrations(): Promise<IntegrationConnection[]> {
      const rows = await clients.integrations.get<IntegrationWire[]>('');
      return rows.map(mapIntegration);
    },
    async connectIntegration(input: ConnectIntegrationInput): Promise<IntegrationConnection> {
      const row = await clients.integrations.post<IntegrationWire>('', {
        slug: input.slug,
        config: input.config,
        credential: { name: input.credentialName, data: input.credential },
      });
      return mapIntegration(row);
    },
    async testIntegration(connectionId: string): Promise<IntegrationTestResult> {
      const result = await clients.integrations.post<IntegrationTestWire>(
        `/${encodeURIComponent(connectionId)}/test`,
      );
      return mapTestResult(result);
    },
    async startEnrollment(slug: string, credentialName: string): Promise<Enrollment> {
      const row = await clients.integrations.post<EnrollmentWire>('/enrollments', {
        slug,
        credential_name: credentialName,
      });
      return mapEnrollment(row);
    },
    async getEnrollment(enrollmentId: string): Promise<Enrollment> {
      const row = await clients.integrations.get<EnrollmentWire>(
        `/enrollments/${encodeURIComponent(enrollmentId)}`,
      );
      return mapEnrollment(row);
    },
    async cancelEnrollment(enrollmentId: string): Promise<Enrollment> {
      const row = await clients.integrations.delete<EnrollmentWire>(
        `/enrollments/${encodeURIComponent(enrollmentId)}`,
      );
      return mapEnrollment(row);
    },
    async submitEnrollmentCode(enrollmentId: string, code: string): Promise<Enrollment> {
      const row = await clients.integrations.post<EnrollmentWire>(
        `/enrollments/${encodeURIComponent(enrollmentId)}/code`,
        { code },
      );
      return mapEnrollment(row);
    },
  };
}
