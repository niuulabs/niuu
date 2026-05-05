import { defineConfig } from 'tsup';
import { execSync } from 'node:child_process';

export default defineConfig({
  entry: ['src/index.ts'],
  format: ['esm'],
  dts: true,
  sourcemap: true,
  clean: true,
  external: ['react'],
  onSuccess: async () => {
    execSync('postcss src/styles.css -o dist/styles.css', {
      stdio: 'inherit',
    });
  },
});
