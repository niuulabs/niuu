/**
 * HTTP Client Factory
 *
 * Creates API clients scoped to a specific backend base path.
 * Each service has its own client: /api/v1/volundr, /api/v1/tyr, etc.
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

/**
 * Create an API client for a specific service base path.
 * @param basePath - e.g. '/api/v1/volundr'
 */
export function createApiClient(basePath: string): ApiClient {
  async function request<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
    const url = `${basePath}${endpoint}`;

    const headers: Record<string, string> = {
      ...(options.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
      ...(options.headers as Record<string, string>),
    };

    const token = tokenProvider?.();
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }

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
