import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { ConfigProvider, resolveSafeConfigEndpoint, useConfig } from './ConfigProvider';

function ConfigReader() {
  const cfg = useConfig();
  return <span data-testid="theme">{cfg.theme}</span>;
}

describe('ConfigProvider', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn());
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('uses the value prop synchronously when provided', () => {
    render(
      <ConfigProvider value={{ theme: 'ice', plugins: {}, services: {} }}>
        <ConfigReader />
      </ConfigProvider>,
    );
    expect(screen.getByTestId('theme').textContent).toBe('ice');
  });

  it('fetches and validates config from the endpoint', async () => {
    const mock = globalThis.fetch as ReturnType<typeof vi.fn>;
    mock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ theme: 'amber', plugins: {}, services: {} }),
    });
    render(
      <ConfigProvider endpoint="/config.json" fallback={<span>loading</span>}>
        <ConfigReader />
      </ConfigProvider>,
    );
    expect(screen.getByText('loading')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId('theme').textContent).toBe('amber'));
  });

  it('renders the errorFallback on a non-ok response', async () => {
    const mock = globalThis.fetch as ReturnType<typeof vi.fn>;
    mock.mockResolvedValueOnce({
      ok: false,
      status: 500,
      json: async () => ({}),
    });
    render(
      <ConfigProvider
        endpoint="/config.json"
        fallback={<span>loading</span>}
        errorFallback={(e) => <span data-testid="err">{e.message}</span>}
      >
        <ConfigReader />
      </ConfigProvider>,
    );
    await waitFor(() => expect(screen.getByTestId('err')).toBeInTheDocument());
  });

  it('throws when useConfig is used outside the provider', () => {
    const error = console.error;
    console.error = () => {};
    expect(() => render(<ConfigReader />)).toThrow(/ConfigProvider/);
    console.error = error;
  });

  it('renders the error fallback when the endpoint leaves the current origin', async () => {
    render(
      <ConfigProvider
        endpoint="https://evil.example/config.json"
        errorFallback={(e) => <span data-testid="err">{e.message}</span>}
      >
        <ConfigReader />
      </ConfigProvider>,
    );

    await waitFor(() => expect(screen.getByTestId('err')).toHaveTextContent('current origin'));
  });
});

describe('resolveSafeConfigEndpoint', () => {
  it('accepts the default config endpoint', () => {
    expect(resolveSafeConfigEndpoint('/config.json')).toBe('/config.json');
  });

  it('normalizes an empty endpoint back to the default path', () => {
    expect(resolveSafeConfigEndpoint('')).toBe('/config.json');
  });

  it('rejects same-origin non-default URLs', () => {
    expect(() =>
      resolveSafeConfigEndpoint('http://localhost:3000/config.live.json?ts=1', {
        origin: 'http://localhost:3000',
      }),
    ).toThrow(/only supports the default/);
  });

  it('rejects root-relative non-default URLs', () => {
    expect(() =>
      resolveSafeConfigEndpoint('/config.live.json', {
        origin: 'http://localhost:3000',
      }),
    ).toThrow(/only supports the default/);
  });

  it('rejects cross-origin URLs', () => {
    expect(() =>
      resolveSafeConfigEndpoint('https://evil.example/config.json', {
        origin: 'http://localhost:3000',
      }),
    ).toThrow(/current origin/);
  });
});
