import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';

/**
 * Recursively concatenates all .css files found under `dir` into a single
 * string suitable for writing to `dist/styles.css` from a tsup `onSuccess`
 * hook.
 */
export function concatCssFiles(dir: string): string {
  const out: string[] = [];
  const walk = (d: string) => {
    for (const entry of readdirSync(d, { withFileTypes: true })) {
      const p = join(d, entry.name);
      if (entry.isDirectory()) {
        walk(p);
      } else if (entry.isFile() && p.endsWith('.css')) {
        out.push(`/* ${p} */\n${readFileSync(p, 'utf8')}`);
      }
    }
  };
  walk(dir);
  return out.join('\n\n');
}
