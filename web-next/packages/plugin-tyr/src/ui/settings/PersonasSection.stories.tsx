import type { Meta, StoryObj } from '@storybook/react';
import type { TyrPersonaSummary } from '../../ports';
import { buildWrapper } from './storyWrappers';
import { PersonasSection } from './PersonasSection';

const SEED_PERSONAS: TyrPersonaSummary[] = [
  {
    name: 'coder',
    permissionMode: 'workspace_write',
    allowedTools: ['Bash', 'Read', 'Write', 'Edit'],
    iterationBudget: 40,
    isBuiltin: true,
    hasOverride: false,
    producesEvent: 'code.changed',
    consumesEvents: [],
  },
  {
    name: 'reviewer',
    permissionMode: 'read_only',
    allowedTools: ['Read', 'Grep', 'Glob'],
    iterationBudget: 20,
    isBuiltin: true,
    hasOverride: true,
    producesEvent: 'review.complete',
    consumesEvents: ['code.changed'],
  },
  {
    name: 'custom-agent',
    permissionMode: 'read_only',
    allowedTools: ['Read'],
    iterationBudget: 10,
    isBuiltin: false,
    hasOverride: false,
    producesEvent: '',
    consumesEvents: [],
  },
];

const meta: Meta<typeof PersonasSection> = {
  title: 'Plugins / Tyr / Settings / PersonasSection',
  component: PersonasSection,
};
export default meta;

type Story = StoryObj<typeof PersonasSection>;

export const Data: Story = {
  decorators: [
    (Story) => {
      const Wrapper = buildWrapper({
        'ravn.personas': {
          async listPersonas() {
            return SEED_PERSONAS;
          },
          async getPersonaYaml(name: string) {
            return `name: ${name}\nmodel: claude-sonnet-4-6\niterationBudget: 40\n`;
          },
        },
      });
      return (
        <Wrapper>
          <Story />
        </Wrapper>
      );
    },
  ],
};

export const Loading: Story = {
  decorators: [
    (Story) => {
      const Wrapper = buildWrapper({
        'ravn.personas': {
          listPersonas() {
            return new Promise(() => {
              /* never resolves */
            });
          },
        },
      });
      return (
        <Wrapper>
          <Story />
        </Wrapper>
      );
    },
  ],
};

export const Error: Story = {
  decorators: [
    (Story) => {
      const Wrapper = buildWrapper({
        'ravn.personas': {
          async listPersonas() {
            throw new Error('Ravn persona service unreachable');
          },
        },
      });
      return (
        <Wrapper>
          <Story />
        </Wrapper>
      );
    },
  ],
};

export const Empty: Story = {
  decorators: [
    (Story) => {
      const Wrapper = buildWrapper({
        'ravn.personas': {
          async listPersonas() {
            return [];
          },
          async getPersonaYaml() {
            return '';
          },
        },
      });
      return (
        <Wrapper>
          <Story />
        </Wrapper>
      );
    },
  ],
};
