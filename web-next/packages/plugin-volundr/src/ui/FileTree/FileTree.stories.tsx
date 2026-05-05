import type { Meta, StoryObj } from '@storybook/react';
import { useState } from 'react';
import { FileTree } from './FileTree';
import { FileViewer } from './FileViewer';
import type { FileTreeNode } from '../../ports/IFileSystemPort';

// ---------------------------------------------------------------------------
// Seed data
// ---------------------------------------------------------------------------

const WORKSPACE_NODES: FileTreeNode[] = [
  {
    name: 'src',
    path: '/workspace/src',
    kind: 'directory',
    children: [
      { name: 'index.ts', path: '/workspace/src/index.ts', kind: 'file', size: 512 },
      { name: 'app.tsx', path: '/workspace/src/app.tsx', kind: 'file', size: 2_048 },
      {
        name: 'components',
        path: '/workspace/src/components',
        kind: 'directory',
        children: [
          {
            name: 'Button.tsx',
            path: '/workspace/src/components/Button.tsx',
            kind: 'file',
            size: 800,
          },
        ],
      },
    ],
  },
  { name: 'package.json', path: '/workspace/package.json', kind: 'file', size: 1_200 },
  { name: 'README.md', path: '/workspace/README.md', kind: 'file', size: 3_000 },
];

const WITH_MOUNTS_NODES: FileTreeNode[] = [
  ...WORKSPACE_NODES,
  {
    name: 'data',
    path: '/mnt/pvc-data',
    kind: 'directory',
    mountName: 'pvc-data',
    children: [
      {
        name: 'dataset.csv',
        path: '/mnt/pvc-data/dataset.csv',
        kind: 'file',
        size: 50_000_000,
        mountName: 'pvc-data',
      },
    ],
  },
  {
    name: 'env',
    path: '/mnt/secrets',
    kind: 'directory',
    mountName: 'api-secrets',
    isSecret: true,
    children: [
      {
        name: 'API_KEY',
        path: '/mnt/secrets/API_KEY',
        kind: 'file',
        isSecret: true,
        mountName: 'api-secrets',
      },
      {
        name: 'DB_PASSWORD',
        path: '/mnt/secrets/DB_PASSWORD',
        kind: 'file',
        isSecret: true,
        mountName: 'api-secrets',
      },
    ],
  },
];

const SAMPLE_CONTENT: Record<string, string> = {
  '/workspace/src/index.ts': `import { App } from './app';

const app = new App();
app.listen(8080);
`,
  '/workspace/src/app.tsx': `import React from 'react';

export function App() {
  return <div>Hello, world!</div>;
}
`,
  '/workspace/package.json': `{
  "name": "my-project",
  "version": "1.0.0",
  "type": "module"
}
`,
};

// ---------------------------------------------------------------------------
// Stories
// ---------------------------------------------------------------------------

const meta: Meta<typeof FileTree> = {
  title: 'Plugins / Völundr / FileTree',
  component: FileTree,
  parameters: { layout: 'padded' },
};
export default meta;

type Story = StoryObj<typeof FileTree>;

/** Plain workspace files — no mounts. */
export const Default: Story = {
  args: { nodes: WORKSPACE_NODES },
};

/** Tree with PVC data mount and a secret mount. Secret content is masked. */
export const WithMounts: Story = {
  args: { nodes: WITH_MOUNTS_NODES },
};

/** Empty workspace — shows the empty state message. */
export const Empty: Story = {
  args: { nodes: [] },
};

function FileTreeWithViewer() {
  const [activePath, setActivePath] = useState<string | undefined>(undefined);
  const [content, setContent] = useState<string>('');
  const [loading, setLoading] = useState(false);

  function handleOpenFile(path: string) {
    setActivePath(path);
    setLoading(true);
    setTimeout(() => {
      setContent(SAMPLE_CONTENT[path] ?? '# (no preview available)');
      setLoading(false);
    }, 400);
  }

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '220px 1fr', gap: 12, height: 480 }}>
      <FileTree nodes={WITH_MOUNTS_NODES} onOpenFile={handleOpenFile} activePath={activePath} />
      {activePath && (
        <FileViewer
          path={activePath}
          content={content}
          isLoading={loading}
          onClose={() => setActivePath(undefined)}
        />
      )}
    </div>
  );
}

/** Interactive: click a file to open it in the FileViewer panel. */
export const WithViewer: Story = {
  render: () => <FileTreeWithViewer />,
};
