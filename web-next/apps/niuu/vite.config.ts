import { resolve } from 'node:path';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const workspaceAlias = [
  {
    find: '@niuulabs/plugin-bifrost/plugin',
    replacement: resolve(__dirname, '../../packages/plugin-bifrost/src/plugin.tsx'),
  },
  {
    find: '@niuulabs/design-tokens/tokens.css',
    replacement: resolve(__dirname, '../../packages/design-tokens/src/tokens.css'),
  },
  {
    find: '@niuulabs/ui/styles.css',
    replacement: resolve(__dirname, '../../packages/ui/dist/styles.css'),
  },
  {
    find: '@niuulabs/shell/styles.css',
    replacement: resolve(__dirname, '../../packages/shell/dist/styles.css'),
  },
  {
    find: '@niuulabs/shell/index.css',
    replacement: resolve(__dirname, '../../packages/shell/dist/index.css'),
  },
  {
    find: '@niuulabs/plugin-login/styles.css',
    replacement: resolve(__dirname, '../../packages/plugin-login/dist/styles.css'),
  },
  {
    find: '@niuulabs/plugin-mimir/styles.css',
    replacement: resolve(__dirname, '../../packages/plugin-mimir/dist/styles.css'),
  },
  {
    find: '@niuulabs/plugin-mimir/index.css',
    replacement: resolve(__dirname, '../../packages/plugin-mimir/dist/index.css'),
  },
  {
    find: '@niuulabs/plugin-observatory/styles.css',
    replacement: resolve(__dirname, '../../packages/plugin-observatory/dist/styles.css'),
  },
  {
    find: '@niuulabs/plugin-observatory/index.css',
    replacement: resolve(__dirname, '../../packages/plugin-observatory/dist/index.css'),
  },
  {
    find: '@niuulabs/plugin-ravn/styles.css',
    replacement: resolve(__dirname, '../../packages/plugin-ravn/dist/styles.css'),
  },
  {
    find: '@niuulabs/plugin-ravn/index.css',
    replacement: resolve(__dirname, '../../packages/plugin-ravn/dist/index.css'),
  },
  {
    find: '@niuulabs/plugin-tyr/styles.css',
    replacement: resolve(__dirname, '../../packages/plugin-tyr/dist/styles.css'),
  },
  {
    find: '@niuulabs/plugin-tyr/index.css',
    replacement: resolve(__dirname, '../../packages/plugin-tyr/dist/index.css'),
  },
  {
    find: '@niuulabs/plugin-volundr/styles.css',
    replacement: resolve(__dirname, '../../packages/plugin-volundr/dist/index.css'),
  },
  {
    find: '@niuulabs/plugin-volundr/index.css',
    replacement: resolve(__dirname, '../../packages/plugin-volundr/dist/index.css'),
  },
  {
    find: '@niuulabs/auth',
    replacement: resolve(__dirname, '../../packages/auth/src/index.ts'),
  },
  {
    find: '@niuulabs/design-tokens',
    replacement: resolve(__dirname, '../../packages/design-tokens/src/index.ts'),
  },
  {
    find: '@niuulabs/domain',
    replacement: resolve(__dirname, '../../packages/domain/src/index.ts'),
  },
  {
    find: '@niuulabs/plugin-bifrost',
    replacement: resolve(__dirname, '../../packages/plugin-bifrost/src/index.ts'),
  },
  {
    find: '@niuulabs/plugin-hello',
    replacement: resolve(__dirname, '../../packages/plugin-hello/src/index.tsx'),
  },
  {
    find: '@niuulabs/plugin-login',
    replacement: resolve(__dirname, '../../packages/plugin-login/src/index.ts'),
  },
  {
    find: '@niuulabs/plugin-mimir',
    replacement: resolve(__dirname, '../../packages/plugin-mimir/src/index.tsx'),
  },
  {
    find: '@niuulabs/plugin-observatory',
    replacement: resolve(__dirname, '../../packages/plugin-observatory/src/index.tsx'),
  },
  {
    find: '@niuulabs/plugin-ravn',
    replacement: resolve(__dirname, '../../packages/plugin-ravn/src/index.ts'),
  },
  {
    find: '@niuulabs/plugin-sdk',
    replacement: resolve(__dirname, '../../packages/plugin-sdk/src/index.ts'),
  },
  {
    find: '@niuulabs/plugin-tyr',
    replacement: resolve(__dirname, '../../packages/plugin-tyr/src/index.ts'),
  },
  {
    find: '@niuulabs/plugin-volundr',
    replacement: resolve(__dirname, '../../packages/plugin-volundr/src/index.ts'),
  },
  {
    find: '@niuulabs/query',
    replacement: resolve(__dirname, '../../packages/query/src/index.ts'),
  },
  {
    find: '@niuulabs/shell',
    replacement: resolve(__dirname, '../../packages/shell/src/index.ts'),
  },
  {
    find: '@niuulabs/ui',
    replacement: resolve(__dirname, '../../packages/ui/src/index.ts'),
  },
] as const;

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: workspaceAlias,
  },
  server: {
    port: 5173,
    strictPort: true,
  },
});
