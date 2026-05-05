import type { Meta, StoryObj } from '@storybook/react';
import { fn } from '@storybook/test';
import { TemplateCard } from './TemplateCard';
import type { Template } from '../domain/template';

const DEFAULT_TEMPLATE: Template = {
  id: 'tpl-default',
  name: 'default',
  version: 1,
  spec: {
    image: 'ghcr.io/niuulabs/skuld',
    tag: 'latest',
    mounts: [],
    env: { API_URL: 'https://api.niuu.world', TOKEN: 'secret' },
    envSecretRefs: ['TOKEN'],
    tools: ['bash', 'python', 'git'],
    resources: {
      cpuRequest: '1',
      cpuLimit: '2',
      memRequestMi: 512,
      memLimitMi: 1_024,
      gpuCount: 0,
    },
    ttlSec: 3_600,
    idleTimeoutSec: 600,
    clusterAffinity: ['cl-eitri'],
  },
  createdAt: '2026-01-01T00:00:00Z',
  updatedAt: '2026-03-15T00:00:00Z',
};

const GPU_TEMPLATE: Template = {
  ...DEFAULT_TEMPLATE,
  id: 'tpl-gpu',
  name: 'gpu-workload',
  version: 2,
  spec: {
    ...DEFAULT_TEMPLATE.spec,
    resources: { ...DEFAULT_TEMPLATE.spec.resources, gpuCount: 2 },
    ttlSec: 7_200,
  },
};

const meta: Meta<typeof TemplateCard> = {
  title: 'Völundr/TemplateCard',
  component: TemplateCard,
  args: {
    onEdit: fn(),
    onClone: fn(),
  },
  parameters: { layout: 'padded' },
};
export default meta;

type Story = StoryObj<typeof TemplateCard>;

export const Default: Story = {
  args: { template: DEFAULT_TEMPLATE },
};

export const WithGpu: Story = {
  args: { template: GPU_TEMPLATE },
};

export const Cloning: Story = {
  args: { template: DEFAULT_TEMPLATE, isCloning: true },
};

export const NoToolsNoEnv: Story = {
  args: {
    template: {
      ...DEFAULT_TEMPLATE,
      spec: { ...DEFAULT_TEMPLATE.spec, tools: [], env: {}, envSecretRefs: [] },
    },
  },
};
