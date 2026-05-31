#!/usr/bin/env bash
#
# Scaffold a new UI module.
#
# Usage:
#   ./scripts/scaffold-module.sh <module-name>
#
# Example:
#   ./scripts/scaffold-module.sh heimdall
#
# Creates the module directory structure under web/src/modules/<name>/
# and registers it in modules/index.ts.

set -euo pipefail

if [ $# -lt 1 ]; then
  echo "Usage: $0 <module-name>"
  echo "Example: $0 heimdall"
  exit 1
fi

NAME="$1"
# Capitalise first letter for component names
LABEL="$(echo "${NAME:0:1}" | tr '[:lower:]' '[:upper:]')${NAME:1}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
MODULE_DIR="$ROOT_DIR/web/src/modules/$NAME"

if [ -d "$MODULE_DIR" ]; then
  echo "Error: Module directory already exists: $MODULE_DIR"
  exit 1
fi

echo "Scaffolding module: $NAME ($LABEL)"

# Create directory structure
mkdir -p "$MODULE_DIR"/{adapters/api,adapters/mock,models,pages,ports,store}

# ── register.ts ────────────────────────────────────────────────────
cat > "$MODULE_DIR/register.ts" << EOF
import { Box } from 'lucide-react';
import { registerModuleDefinition } from '@/modules/shared/registry';

registerModuleDefinition({
  key: '$NAME',
  label: '$LABEL',
  icon: Box,
  basePath: '/$NAME',
  routes: [
    {
      path: '',
      load: () => import('./pages/${LABEL}Page').then(m => ({ default: m.${LABEL}Page })),
    },
  ],
  // Uncomment to add settings/admin sections:
  // sections: [],
});
EOF

# ── ports ──────────────────────────────────────────────────────────
cat > "$MODULE_DIR/ports/${NAME}.port.ts" << EOF
/**
 * Port interface for the $LABEL service.
 * Define your service methods here.
 */
export interface I${LABEL}Service {
  // Add service methods here
}
EOF

# ── models ─────────────────────────────────────────────────────────
cat > "$MODULE_DIR/models/${NAME}.model.ts" << EOF
/**
 * Domain models for the $LABEL module.
 */
EOF

# ── adapters/index.ts ──────────────────────────────────────────────
cat > "$MODULE_DIR/adapters/index.ts" << EOF
import type { I${LABEL}Service } from '@/modules/$NAME/ports/${NAME}.port';
import { Api${LABEL}Service } from './api/${NAME}.adapter';
import { Mock${LABEL}Service } from './mock/${NAME}.adapter';

function shouldUseRealApi(): boolean {
  if (import.meta.env.PROD) return true;
  return import.meta.env.VITE_USE_REAL_API === 'true';
}

export const ${NAME}Service: I${LABEL}Service = shouldUseRealApi()
  ? new Api${LABEL}Service()
  : new Mock${LABEL}Service();
EOF

# ── API adapter ────────────────────────────────────────────────────
cat > "$MODULE_DIR/adapters/api/${NAME}.adapter.ts" << EOF
import { createApiClient } from '@/modules/shared/api/client';
import type { I${LABEL}Service } from '@/modules/$NAME/ports/${NAME}.port';

const api = createApiClient('/api/v1/$NAME');

export class Api${LABEL}Service implements I${LABEL}Service {
  // Implement service methods here
}
EOF

# ── Mock adapter ───────────────────────────────────────────────────
cat > "$MODULE_DIR/adapters/mock/${NAME}.adapter.ts" << EOF
import type { I${LABEL}Service } from '@/modules/$NAME/ports/${NAME}.port';

export class Mock${LABEL}Service implements I${LABEL}Service {
  // Implement mock service methods here
}
EOF

# ── Page component ─────────────────────────────────────────────────
cat > "$MODULE_DIR/pages/${LABEL}Page.tsx" << EOF
import styles from './${LABEL}Page.module.css';

export function ${LABEL}Page() {
  return (
    <div className={styles.page}>
      <h1 className={styles.title}>$LABEL</h1>
      <p className={styles.subtitle}>Module is ready. Start building.</p>
    </div>
  );
}
EOF

cat > "$MODULE_DIR/pages/${LABEL}Page.module.css" << EOF
.page {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  gap: var(--space-4);
}

.title {
  font-size: var(--text-2xl);
  font-weight: 600;
  color: var(--color-text-primary);
}

.subtitle {
  font-size: var(--text-base);
  color: var(--color-text-secondary);
}
EOF

# ── Store ──────────────────────────────────────────────────────────
cat > "$MODULE_DIR/store/${NAME}.store.ts" << EOF
import { create } from 'zustand';

interface ${LABEL}State {
  loading: boolean;
}

export const use${LABEL}Store = create<${LABEL}State>()(() => ({
  loading: false,
}));
EOF

# ── Register in modules/index.ts ──────────────────────────────────
INDEX_FILE="$ROOT_DIR/web/src/modules/index.ts"
# Add import after the last register import
if grep -q "import './$NAME/register';" "$INDEX_FILE"; then
  echo "Module already registered in $INDEX_FILE"
else
  sed -i "/import '\.\/ting\/register';/a import './$NAME/register';" "$INDEX_FILE"
fi

echo ""
echo "Module '$NAME' scaffolded successfully!"
echo ""
echo "Files created:"
find "$MODULE_DIR" -type f | sort | sed "s|$ROOT_DIR/||"
echo ""
echo "Next steps:"
echo "  1. Edit web/src/modules/$NAME/register.ts to configure routes, sections, and proxies"
echo "  2. Define your service interface in ports/${NAME}.port.ts"
echo "  3. Implement adapters in adapters/api/ and adapters/mock/"
echo "  4. Build your pages in pages/"
echo "  5. Run 'cd web && npm run dev' to see your module in the sidebar"
