/**
 * HTTP Client Factory
 *
 * Creates API clients scoped to a specific backend base path.
 * Each service has its own client: /api/v1/forge, /api/v1/ting, etc.
 */

export interface ApiError {
  detail: string;
}

export class ApiClientError extends Error {
  status: number;
  detail?: string;

  constructor(message: string, status: number, detail?: string) {
    super(message);
    this.name = 'ApiClientError';
    this.status = status;
    this.detail = detail;
  }
}

export interface ApiClient {
  basePath?: string;
  get<T>(endpoint: string): Promise<T>;
  post<T>(endpoint: string, body?: unknown): Promise<T>;
  put<T>(endpoint: string, body: unknown): Promise<T>;
  patch<T>(endpoint: string, body: unknown): Promise<T>;
  delete<T>(endpoint: string, body?: unknown): Promise<T>;
}

/**
 * Global token provider. Set by AuthProvider to inject Bearer tokens.
 * Returns null when auth is disabled (dev / allow-all mode).
 */
let tokenProvider: (() => string | null) | null = null;
let cachedDevIdentityOverride: DevIdentityOverride | null = null;
const DEV_IDENTITY_STORAGE_KEY = 'niuu.devIdentityOverride';

interface DevIdentityOverride {
  userId: string;
  email: string;
  tenantId: string;
  roles: string[];
}

function isLanDevHostname(hostname: string): boolean {
  if (['localhost', '127.0.0.1', '::1'].includes(hostname)) {
    return true;
  }

  const ipv4Match = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(hostname.trim());
  if (!ipv4Match) {
    return false;
  }

  const octets = ipv4Match.slice(1).map((part) => Number(part));
  if (octets.some((octet) => Number.isNaN(octet) || octet < 0 || octet > 255)) {
    return false;
  }

  const [firstOctet = -1, secondOctet = -1] = octets;

  return (
    firstOctet === 10 ||
    (firstOctet === 172 && secondOctet >= 16 && secondOctet <= 31) ||
    (firstOctet === 192 && secondOctet === 168)
  );
}

function canUseBrowserStorage(): boolean {
  return typeof window !== 'undefined' && typeof window.sessionStorage !== 'undefined';
}

function persistDevIdentityOverride(identity: DevIdentityOverride): void {
  cachedDevIdentityOverride = identity;
  if (!canUseBrowserStorage()) return;
  try {
    window.sessionStorage.setItem(DEV_IDENTITY_STORAGE_KEY, JSON.stringify(identity));
  } catch {
    // Best-effort local dev convenience only.
  }
}

function readStoredDevIdentityOverride(): DevIdentityOverride | null {
  if (cachedDevIdentityOverride) return cachedDevIdentityOverride;
  if (!canUseBrowserStorage()) return null;
  try {
    const raw = window.sessionStorage.getItem(DEV_IDENTITY_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<DevIdentityOverride>;
    if (typeof parsed.userId !== 'string' || !parsed.userId.trim()) return null;
    const identity: DevIdentityOverride = {
      userId: parsed.userId.trim(),
      email: typeof parsed.email === 'string' ? parsed.email.trim() : '',
      tenantId: typeof parsed.tenantId === 'string' ? parsed.tenantId.trim() : '',
      roles: Array.isArray(parsed.roles)
        ? parsed.roles
            .filter((role): role is string => typeof role === 'string')
            .map((role) => role.trim())
            .filter(Boolean)
        : ['volundr:developer'],
    };
    cachedDevIdentityOverride = identity;
    return identity;
  } catch {
    return null;
  }
}

/**
 * Register a function that returns the current access token.
 * Called once by AuthProvider on mount.
 */
export function setTokenProvider(provider: (() => string | null) | null): void {
  tokenProvider = provider;
}

/**
 * Return the current access token, or null when auth is disabled.
 */
export function getAccessToken(): string | null {
  return tokenProvider?.() ?? null;
}

function readDevIdentityOverride(): DevIdentityOverride | null {
  if (typeof window === 'undefined') {
    return null;
  }

  if (!isLanDevHostname(window.location.hostname)) {
    return null;
  }

  const params = new URLSearchParams(window.location.search);
  const userId = params.get('devUserId')?.trim() ?? '';
  if (!userId) {
    return readStoredDevIdentityOverride();
  }

  const rawRoles = params.get('devRoles')?.trim() ?? 'volundr:developer';
  const identity = {
    userId,
    email: params.get('devEmail')?.trim() ?? '',
    tenantId: params.get('devTenantId')?.trim() ?? '',
    roles: rawRoles
      .split(',')
      .map((role) => role.trim())
      .filter(Boolean),
  };
  persistDevIdentityOverride(identity);
  return identity;
}

export function getAuthHeaders(headers: HeadersInit = {}): Headers {
  const nextHeaders = new Headers(headers);
  const token = getAccessToken();
  if (token) {
    nextHeaders.set('Authorization', `Bearer ${token}`);
    return nextHeaders;
  }

  const devIdentity = readDevIdentityOverride();
  if (!devIdentity) {
    return nextHeaders;
  }

  nextHeaders.set('x-auth-user-id', devIdentity.userId);
  if (devIdentity.email) nextHeaders.set('x-auth-email', devIdentity.email);
  if (devIdentity.tenantId) nextHeaders.set('x-auth-tenant', devIdentity.tenantId);
  if (devIdentity.roles.length > 0) {
    nextHeaders.set('x-auth-roles', devIdentity.roles.join(','));
  }
  return nextHeaders;
}

export function withAuthQuery(url: string): string {
  const baseOrigin = typeof window === 'undefined' ? 'http://localhost' : window.location.origin;
  const resolved = new URL(url, baseOrigin);
  const token = getAccessToken();
  if (token) {
    resolved.searchParams.set('access_token', token);
    return resolved.toString();
  }

  const devIdentity = readDevIdentityOverride();
  if (!devIdentity) {
    return resolved.toString();
  }

  resolved.searchParams.set('devUserId', devIdentity.userId);
  if (devIdentity.email) resolved.searchParams.set('devEmail', devIdentity.email);
  if (devIdentity.tenantId) resolved.searchParams.set('devTenantId', devIdentity.tenantId);
  if (devIdentity.roles.length > 0) {
    resolved.searchParams.set('devRoles', devIdentity.roles.join(','));
  }
  return resolved.toString();
}

export function getWebSocketAuth(url: string): { url: string; protocols?: string[] } {
  const baseOrigin = typeof window === 'undefined' ? 'http://localhost' : window.location.origin;
  const resolved = new URL(url, baseOrigin);
  const token = getAccessToken();
  if (token) {
    return {
      url: resolved.toString(),
      protocols: [`volundr.bearer.${encodeURIComponent(token)}`],
    };
  }

  return { url: withAuthQuery(url) };
}

/**
 * Create an API client for a specific service base path.
 * @param basePath - e.g. '/api/v1/forge'
 */
export function createApiClient(basePath: string): ApiClient {
  async function request<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
    const url = `${basePath}${endpoint}`;

    const headers = getAuthHeaders({
      ...(options.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
      ...(options.headers as Record<string, string>),
    });

    const config: RequestInit = { ...options, headers };
    const response = await fetch(url, config);

    if (response.status === 204) {
      return undefined as T;
    }

    const data = await response.json();

    if (!response.ok) {
      const errorDetail = (data as ApiError)?.detail ?? 'Unknown error';
      throw new ApiClientError(
        `API request failed: ${response.status}`,
        response.status,
        errorDetail,
      );
    }

    return data as T;
  }

  return {
    basePath,
    get<T>(endpoint: string): Promise<T> {
      return request<T>(endpoint, { method: 'GET' });
    },
    post<T>(endpoint: string, body?: unknown): Promise<T> {
      if (body instanceof FormData) {
        return request<T>(endpoint, { method: 'POST', body });
      }
      return request<T>(endpoint, {
        method: 'POST',
        body: body ? JSON.stringify(body) : undefined,
      });
    },
    put<T>(endpoint: string, body: unknown): Promise<T> {
      return request<T>(endpoint, { method: 'PUT', body: JSON.stringify(body) });
    },
    patch<T>(endpoint: string, body: unknown): Promise<T> {
      return request<T>(endpoint, { method: 'PATCH', body: JSON.stringify(body) });
    },
    delete<T>(endpoint: string, body?: unknown): Promise<T> {
      const opts: RequestInit = { method: 'DELETE' };
      if (body !== undefined) {
        opts.headers = { 'Content-Type': 'application/json' };
        opts.body = JSON.stringify(body);
      }
      return request<T>(endpoint, opts);
    },
  };
}
