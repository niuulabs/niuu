import type { Meta, StoryObj } from '@storybook/react';
import { useEffect } from 'react';
import {
  CommandPaletteProvider,
  useCommandPalette,
  useCommandPaletteRegistry,
  type Command,
  type CommandPaletteProviderProps,
} from './CommandPalette';

// ---------------------------------------------------------------------------
// Storybook config
// ---------------------------------------------------------------------------

const meta: Meta<typeof CommandPaletteProvider> = {
  title: 'Overlays/CommandPalette',
  component: CommandPaletteProvider,
  parameters: { a11y: {} },
};
export default meta;

type Story = StoryObj<typeof CommandPaletteProvider>;

// ---------------------------------------------------------------------------
// Story helpers — inner components to use the context hooks
// ---------------------------------------------------------------------------

const DEMO_COMMANDS: Command[] = [
  {
    id: 'nav-dashboard',
    title: 'Go to Dashboard',
    subtitle: 'dashboard',
    keywords: ['navigate', 'home'],
    execute: () => alert('Navigate → Dashboard'),
  },
  {
    id: 'nav-settings',
    title: 'Go to Settings',
    subtitle: 'settings',
    keywords: ['navigate', 'prefs', 'preferences'],
    execute: () => alert('Navigate → Settings'),
  },
  {
    id: 'nav-observatory',
    title: 'Go to Observatory',
    subtitle: 'observatory · topology',
    keywords: ['navigate', 'topology', 'mesh'],
    execute: () => alert('Navigate → Observatory'),
  },
  {
    id: 'action-deploy',
    title: 'Deploy to production',
    subtitle: 'deploy · prod',
    keywords: ['ship', 'push', 'release'],
    execute: () => alert('Action → Deploy'),
  },
  {
    id: 'action-run',
    title: 'Run diagnostics',
    subtitle: 'diagnostics',
    keywords: ['check', 'health', 'test'],
    execute: () => alert('Action → Diagnostics'),
  },
];

function CommandRegistrar({ commands }: { commands: Command[] }) {
  const { register, unregister } = useCommandPaletteRegistry();
  useEffect(() => {
    for (const cmd of commands) {
      register(cmd);
    }
    return () => {
      for (const cmd of commands) {
        unregister(cmd.id);
      }
    };
    // Commands are stable demo objects — no need to re-register on every render
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return null;
}

function OpenButton() {
  const { setOpen } = useCommandPalette();
  return (
    <button
      onClick={() => setOpen(true)}
      style={{
        padding: 'var(--space-3) var(--space-5)',
        background: 'var(--color-bg-tertiary)',
        color: 'var(--color-text-primary)',
        border: '1px solid var(--color-border)',
        borderRadius: 'var(--radius-md)',
        fontFamily: 'var(--font-sans)',
        fontSize: 'var(--text-sm)',
        cursor: 'pointer',
      }}
    >
      Open Command Palette &nbsp;<kbd>⌘K</kbd>
    </button>
  );
}

function StoryWrapper({
  commands,
  initialOpen,
}: Pick<CommandPaletteProviderProps, 'initialOpen'> & { commands: Command[] }) {
  return (
    <CommandPaletteProvider initialOpen={initialOpen}>
      <CommandRegistrar commands={commands} />
      <div
        style={{
          minHeight: 120,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        <OpenButton />
      </div>
    </CommandPaletteProvider>
  );
}

// ---------------------------------------------------------------------------
// Stories
// ---------------------------------------------------------------------------

/** Closed state — shows the trigger button. Press ⌘K or click to open. */
export const Closed: Story = {
  render: () => <StoryWrapper commands={DEMO_COMMANDS} />,
};

/** Open with results — 5 demo commands ready to search. */
export const OpenWithResults: Story = {
  render: () => <StoryWrapper commands={DEMO_COMMANDS} initialOpen />,
};

/** Empty state — palette opens but no commands are registered. */
export const EmptyState: Story = {
  render: () => <StoryWrapper commands={[]} initialOpen />,
};

/** Single command — minimal registration example. */
export const SingleCommand: Story = {
  render: () => (
    <StoryWrapper
      commands={[
        {
          id: 'only-one',
          title: 'The only command',
          subtitle: 'there is only one option',
          execute: () => alert('Executed!'),
        },
      ]}
      initialOpen
    />
  ),
};
