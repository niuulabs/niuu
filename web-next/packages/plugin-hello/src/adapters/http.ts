/**
 * HTTP adapter for the Hello service.
 *
 * Accepts any HTTP client that has a `get` method — compatible with
 * `createApiClient(basePath)` from @niuulabs/query.
 */

import type { Greeting, IHelloService } from '../ports';

/** Minimal HTTP client — structurally compatible with ApiClient from @niuulabs/query. */
interface HttpClient {
  get<T>(endpoint: string): Promise<T>;
}

export function buildHelloHttpAdapter(client: HttpClient): IHelloService {
  return {
    async listGreetings(): Promise<Greeting[]> {
      return client.get<Greeting[]>('/greetings');
    },
  };
}
