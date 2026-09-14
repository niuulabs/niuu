import { createRootRoute } from '@tanstack/react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { tingPlugin } from './index';

const mockMode = vi.hoisted(() => ({ current: 'simple' as 'simple' | 'advanced' }));

vi.mock('@niuulabs/shell', async () => {
  const actual = await vi.importActual<Record<string, unknown>>('@niuulabs/shell');
  return { ...actual, readUiMode: () => mockMode.current };
});

const rootRoute = createRootRoute();
const routes = tingPlugin.routes?.(rootRoute) ?? [];

function routeFor(path: string) {
  const route = routes.find((candidate) => candidate.options.path === path);
  if (!route) throw new Error(`no route for ${path}`);
  return route;
}

describe('tingPlugin descriptor', () => {
  beforeEach(() => {
    mockMode.current = 'simple';
  });

  it('shows only the workflows tab in Simple mode', () => {
    expect(tingPlugin.simple).toMatchObject({
      tabs: ['workflows'],
      title: 'Workflows',
      subtitle: 'stages with gates you approve',
    });
  });

  it('routes the builder separately from the workflows page', () => {
    expect(routeFor('/ting/workflows')).toBeDefined();
    expect(routeFor('/ting/workflows/build')).toBeDefined();
  });

  it('sends /ting to the workflows page in Simple mode', () => {
    const beforeLoad = routeFor('/ting').options.beforeLoad as () => void;
    let thrown: unknown;
    try {
      beforeLoad();
    } catch (error) {
      thrown = error;
    }
    expect((thrown as { options?: { to?: string } })?.options?.to).toBe('/ting/workflows');
  });

  it('keeps /ting on the dashboard in Advanced mode', () => {
    mockMode.current = 'advanced';
    const beforeLoad = routeFor('/ting').options.beforeLoad as () => void;
    expect(() => beforeLoad()).not.toThrow();
  });
});
