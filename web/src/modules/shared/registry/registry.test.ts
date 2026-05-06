import { describe, it, expect } from 'vitest';
import { Settings, Compass } from 'lucide-react';
import {
  registerModule,
  getModule,
  getAllModules,
  registerProductModule,
  getProductModules,
  registerModuleDefinition,
  getModuleDefinitions,
  getModuleDefinition,
} from './registry';

const dummyLoad = () =>
  Promise.resolve({
    default: (() => null) as unknown as React.ComponentType,
  });

describe('Module Registry', () => {
  it('registers and retrieves a module', () => {
    registerModule({ key: 'test-module', load: dummyLoad, icon: Settings });

    const entry = getModule('test-module');
    expect(entry).toBeDefined();
    expect(entry!.key).toBe('test-module');
    expect(entry!.icon).toBe(Settings);
  });

  it('returns undefined for unregistered key', () => {
    const entry = getModule('nonexistent-module');
    expect(entry).toBeUndefined();
  });

  it('getAllModules returns all registered modules', () => {
    registerModule({ key: 'reg-test-a', load: dummyLoad, icon: Settings });
    registerModule({ key: 'reg-test-b', load: dummyLoad, icon: Settings });

    const all = getAllModules();
    expect(all.has('reg-test-a')).toBe(true);
    expect(all.has('reg-test-b')).toBe(true);
  });

  it('overwrites existing registration with same key', () => {
    const loadA = () =>
      Promise.resolve({
        default: (() => 'A') as unknown as React.ComponentType,
      });
    const loadB = () =>
      Promise.resolve({
        default: (() => 'B') as unknown as React.ComponentType,
      });

    registerModule({ key: 'overwrite-test', load: loadA, icon: Settings });
    registerModule({ key: 'overwrite-test', load: loadB, icon: Settings });

    const entry = getModule('overwrite-test');
    expect(entry!.load).toBe(loadB);
  });
});

describe('Product Module Registry', () => {
  it('registers and retrieves a product module', () => {
    const dummyProductLoad = () =>
      Promise.resolve({ default: (() => null) as unknown as React.ComponentType });

    registerProductModule({
      key: 'test-product',
      label: 'Test Product',
      icon: Settings,
      basePath: '/test',
      load: dummyProductLoad,
    });

    const modules = getProductModules();
    const found = modules.find(m => m.key === 'test-product');
    expect(found).toBeDefined();
    expect(found!.label).toBe('Test Product');
    expect(found!.basePath).toBe('/test');
    expect(found!.icon).toBe(Settings);
  });

  it('returns all registered product modules', () => {
    const dummyProductLoad = () =>
      Promise.resolve({ default: (() => null) as unknown as React.ComponentType });

    registerProductModule({
      key: 'product-a',
      label: 'Product A',
      icon: Settings,
      basePath: '/a',
      load: dummyProductLoad,
    });
    registerProductModule({
      key: 'product-b',
      label: 'Product B',
      icon: Settings,
      basePath: '/b',
      load: dummyProductLoad,
    });

    const modules = getProductModules();
    expect(modules.some(m => m.key === 'product-a')).toBe(true);
    expect(modules.some(m => m.key === 'product-b')).toBe(true);
  });
});

describe('Module Definition Registry', () => {
  it('registers and retrieves a module definition', () => {
    registerModuleDefinition({
      key: 'def-test',
      label: 'Def Test',
      icon: Compass,
      basePath: '/def-test',
      routes: [{ path: '', load: dummyLoad }],
    });

    const def = getModuleDefinition('def-test');
    expect(def).toBeDefined();
    expect(def!.key).toBe('def-test');
    expect(def!.label).toBe('Def Test');
    expect(def!.basePath).toBe('/def-test');
  });

  it('populates legacy product module registry', () => {
    registerModuleDefinition({
      key: 'def-legacy-product',
      label: 'Legacy Product',
      icon: Compass,
      basePath: '/legacy-product',
      routes: [{ path: '', load: dummyLoad }],
    });

    const products = getProductModules();
    const found = products.find(m => m.key === 'def-legacy-product');
    expect(found).toBeDefined();
    expect(found!.label).toBe('Legacy Product');
    expect(found!.basePath).toBe('/legacy-product');
  });

  it('populates legacy feature module registry from sections', () => {
    const sectionLoad = () =>
      Promise.resolve({ default: (() => null) as unknown as React.ComponentType });

    registerModuleDefinition({
      key: 'def-with-sections',
      label: 'With Sections',
      icon: Compass,
      basePath: '/with-sections',
      routes: [{ path: '', load: dummyLoad }],
      sections: [
        { key: 'my-setting', scope: 'settings', icon: Settings, load: sectionLoad },
        { key: 'my-admin', scope: 'admin', icon: Settings, load: sectionLoad },
      ],
    });

    expect(getModule('my-setting')).toBeDefined();
    expect(getModule('my-admin')).toBeDefined();
  });

  it('getModuleDefinitions returns all registered definitions', () => {
    registerModuleDefinition({
      key: 'def-list-a',
      label: 'A',
      icon: Compass,
      basePath: '/a',
      routes: [{ path: '', load: dummyLoad }],
    });
    registerModuleDefinition({
      key: 'def-list-b',
      label: 'B',
      icon: Compass,
      basePath: '/b',
      routes: [{ path: '', load: dummyLoad }],
    });

    const defs = getModuleDefinitions();
    expect(defs.some(d => d.key === 'def-list-a')).toBe(true);
    expect(defs.some(d => d.key === 'def-list-b')).toBe(true);
  });

  it('handles module with layout', () => {
    const layoutLoad = () =>
      Promise.resolve({ default: (() => null) as unknown as React.ComponentType });

    registerModuleDefinition({
      key: 'def-with-layout',
      label: 'With Layout',
      icon: Compass,
      basePath: '/with-layout',
      layout: layoutLoad,
      routes: [
        { path: '', index: true, redirectTo: 'dashboard' },
        { path: 'dashboard', load: dummyLoad },
      ],
    });

    const def = getModuleDefinition('def-with-layout');
    expect(def!.layout).toBe(layoutLoad);
    expect(def!.routes).toHaveLength(2);
  });

  it('returns undefined for unregistered definition', () => {
    expect(getModuleDefinition('nonexistent-def')).toBeUndefined();
  });

  it('falls back to dummy load when no layout and no route with load', () => {
    registerModuleDefinition({
      key: 'def-no-load',
      label: 'No Load',
      icon: Compass,
      basePath: '/no-load',
      routes: [{ path: '', index: true, redirectTo: 'dashboard' }],
    });

    const products = getProductModules();
    const found = products.find(m => m.key === 'def-no-load');
    expect(found).toBeDefined();
    expect(typeof found!.load).toBe('function');
  });
});
