import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const fromHere = (relativePath: string) => fileURLToPath(new URL(relativePath, import.meta.url));

const workspaceAlias = [
  {
    find: '@niuulabs/plugin-bifrost/plugin',
    replacement: fromHere('../../packages/plugin-bifrost/src/plugin.tsx'),
  },
  {
    find: '@niuulabs/design-tokens/tokens.css',
    replacement: fromHere('../../packages/design-tokens/src/tokens.css'),
  },
  {
    find: '@niuulabs/ui/styles.css',
    replacement: fromHere('../../packages/ui/dist/styles.css'),
  },
  {
    find: '@niuulabs/shell/styles.css',
    replacement: fromHere('../../packages/shell/dist/styles.css'),
  },
  {
    find: '@niuulabs/shell/index.css',
    replacement: fromHere('../../packages/shell/dist/index.css'),
  },
  {
    find: '@niuulabs/plugin-login/styles.css',
    replacement: fromHere('../../packages/plugin-login/dist/styles.css'),
  },
  {
    find: '@niuulabs/plugin-mimir/styles.css',
    replacement: fromHere('../../packages/plugin-mimir/dist/styles.css'),
  },
  {
    find: '@niuulabs/plugin-mimir/index.css',
    replacement: fromHere('../../packages/plugin-mimir/dist/index.css'),
  },
  {
    find: '@niuulabs/plugin-observatory/styles.css',
    replacement: fromHere('../../packages/plugin-observatory/dist/styles.css'),
  },
  {
    find: '@niuulabs/plugin-observatory/index.css',
    replacement: fromHere('../../packages/plugin-observatory/dist/index.css'),
  },
  {
    find: '@niuulabs/plugin-ravn/styles.css',
    replacement: fromHere('../../packages/plugin-ravn/dist/styles.css'),
  },
  {
    find: '@niuulabs/plugin-ravn/index.css',
    replacement: fromHere('../../packages/plugin-ravn/dist/index.css'),
  },
  {
    find: '@niuulabs/plugin-ting/styles.css',
    replacement: fromHere('../../packages/plugin-ting/dist/styles.css'),
  },
  {
    find: '@niuulabs/plugin-ting/index.css',
    replacement: fromHere('../../packages/plugin-ting/dist/index.css'),
  },
  {
    find: '@niuulabs/plugin-volundr/styles.css',
    replacement: fromHere('../../packages/plugin-volundr/dist/index.css'),
  },
  {
    find: '@niuulabs/plugin-volundr/index.css',
    replacement: fromHere('../../packages/plugin-volundr/dist/index.css'),
  },
  {
    find: '@niuulabs/auth',
    replacement: fromHere('../../packages/auth/src/index.ts'),
  },
  {
    find: '@niuulabs/design-tokens',
    replacement: fromHere('../../packages/design-tokens/src/index.ts'),
  },
  {
    find: '@niuulabs/domain',
    replacement: fromHere('../../packages/domain/src/index.ts'),
  },
  {
    find: '@niuulabs/plugin-bifrost',
    replacement: fromHere('../../packages/plugin-bifrost/src/index.ts'),
  },
  {
    find: '@niuulabs/plugin-hello',
    replacement: fromHere('../../packages/plugin-hello/src/index.tsx'),
  },
  {
    find: '@niuulabs/plugin-login',
    replacement: fromHere('../../packages/plugin-login/src/index.ts'),
  },
  {
    find: '@niuulabs/plugin-mimir',
    replacement: fromHere('../../packages/plugin-mimir/src/index.tsx'),
  },
  {
    find: '@niuulabs/plugin-observatory',
    replacement: fromHere('../../packages/plugin-observatory/src/index.tsx'),
  },
  {
    find: '@niuulabs/plugin-ravn',
    replacement: fromHere('../../packages/plugin-ravn/src/index.ts'),
  },
  {
    find: '@niuulabs/plugin-sdk',
    replacement: fromHere('../../packages/plugin-sdk/src/index.ts'),
  },
  {
    find: '@niuulabs/plugin-ting',
    replacement: fromHere('../../packages/plugin-ting/src/index.ts'),
  },
  {
    find: '@niuulabs/plugin-volundr',
    replacement: fromHere('../../packages/plugin-volundr/src/index.ts'),
  },
  {
    find: '@niuulabs/query',
    replacement: fromHere('../../packages/query/src/index.ts'),
  },
  {
    find: '@niuulabs/shell',
    replacement: fromHere('../../packages/shell/src/index.ts'),
  },
  {
    find: '@niuulabs/ui',
    replacement: fromHere('../../packages/ui/src/index.ts'),
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
