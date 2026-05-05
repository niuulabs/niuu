import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { TemplateCard } from './TemplateCard';
import type { Template } from '../domain/template';

const TEMPLATE: Template = {
  id: 'tpl-1',
  name: 'default',
  version: 2,
  spec: {
    image: 'ghcr.io/niuulabs/skuld',
    tag: 'latest',
    mounts: [],
    env: { API_URL: 'https://api.niuu.world', TOKEN: 'secret-value' },
    envSecretRefs: ['TOKEN'],
    tools: ['bash', 'python'],
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
  ...TEMPLATE,
  id: 'tpl-gpu',
  name: 'gpu-workload',
  spec: {
    ...TEMPLATE.spec,
    resources: { ...TEMPLATE.spec.resources, gpuCount: 2 },
  },
};

describe('TemplateCard', () => {
  it('renders the template name', () => {
    render(<TemplateCard template={TEMPLATE} onEdit={vi.fn()} onClone={vi.fn()} />);
    expect(screen.getByText('default')).toBeInTheDocument();
  });

  it('renders the version badge', () => {
    render(<TemplateCard template={TEMPLATE} onEdit={vi.fn()} onClone={vi.fn()} />);
    expect(screen.getByText('v2')).toBeInTheDocument();
  });

  it('renders image:tag', () => {
    render(<TemplateCard template={TEMPLATE} onEdit={vi.fn()} onClone={vi.fn()} />);
    expect(screen.getByText('ghcr.io/niuulabs/skuld:latest')).toBeInTheDocument();
  });

  it('renders CPU resource chip', () => {
    render(<TemplateCard template={TEMPLATE} onEdit={vi.fn()} onClone={vi.fn()} />);
    expect(screen.getByText('CPU 1–2')).toBeInTheDocument();
  });

  it('renders memory resource chip', () => {
    render(<TemplateCard template={TEMPLATE} onEdit={vi.fn()} onClone={vi.fn()} />);
    expect(screen.getByText('Mem 512–1024 Mi')).toBeInTheDocument();
  });

  it('does not render GPU chip when gpuCount is 0', () => {
    render(<TemplateCard template={TEMPLATE} onEdit={vi.fn()} onClone={vi.fn()} />);
    expect(screen.queryByText(/GPU/)).not.toBeInTheDocument();
  });

  it('renders GPU chip when gpuCount > 0', () => {
    render(<TemplateCard template={GPU_TEMPLATE} onEdit={vi.fn()} onClone={vi.fn()} />);
    expect(screen.getByText('GPU ×2')).toBeInTheDocument();
  });

  it('renders TTL and idle timeout', () => {
    render(<TemplateCard template={TEMPLATE} onEdit={vi.fn()} onClone={vi.fn()} />);
    expect(screen.getByText(/60m/)).toBeInTheDocument(); // 3600s = 60m
    expect(screen.getByText(/10m/)).toBeInTheDocument(); // 600s = 10m
  });

  it('renders tool chips', () => {
    render(<TemplateCard template={TEMPLATE} onEdit={vi.fn()} onClone={vi.fn()} />);
    expect(screen.getByText('bash')).toBeInTheDocument();
    expect(screen.getByText('python')).toBeInTheDocument();
  });

  it('renders unmasked env key and masks secret ref values', () => {
    render(<TemplateCard template={TEMPLATE} onEdit={vi.fn()} onClone={vi.fn()} />);
    // API_URL should be visible; TOKEN should be masked
    expect(screen.getByText('API_URL')).toBeInTheDocument();
    expect(screen.getByText('https://api.niuu.world')).toBeInTheDocument();
    expect(screen.getByText('TOKEN')).toBeInTheDocument();
    expect(screen.getByText('***')).toBeInTheDocument();
    expect(screen.queryByText('secret-value')).not.toBeInTheDocument();
  });

  it('renders cluster affinity chips', () => {
    render(<TemplateCard template={TEMPLATE} onEdit={vi.fn()} onClone={vi.fn()} />);
    expect(screen.getByText('cl-eitri')).toBeInTheDocument();
  });

  it('calls onEdit when Edit button is clicked', () => {
    const onEdit = vi.fn();
    render(<TemplateCard template={TEMPLATE} onEdit={onEdit} onClone={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: /edit template default/i }));
    expect(onEdit).toHaveBeenCalledWith(TEMPLATE);
  });

  it('calls onClone when Clone button is clicked', () => {
    const onClone = vi.fn();
    render(<TemplateCard template={TEMPLATE} onEdit={vi.fn()} onClone={onClone} />);
    fireEvent.click(screen.getByRole('button', { name: /clone template default/i }));
    expect(onClone).toHaveBeenCalledWith(TEMPLATE);
  });

  it('disables Clone button and shows Cloning… when isCloning=true', () => {
    render(<TemplateCard template={TEMPLATE} onEdit={vi.fn()} onClone={vi.fn()} isCloning />);
    const cloneBtn = screen.getByRole('button', { name: /clone template default/i });
    expect(cloneBtn).toBeDisabled();
    expect(cloneBtn).toHaveTextContent('Cloning…');
  });

  it('renders as article element', () => {
    render(<TemplateCard template={TEMPLATE} onEdit={vi.fn()} onClone={vi.fn()} />);
    expect(screen.getByRole('article')).toBeInTheDocument();
  });

  it('shows no env section when env is empty', () => {
    const emptyEnvTemplate: Template = {
      ...TEMPLATE,
      spec: { ...TEMPLATE.spec, env: {}, envSecretRefs: [] },
    };
    render(<TemplateCard template={emptyEnvTemplate} onEdit={vi.fn()} onClone={vi.fn()} />);
    expect(screen.queryByRole('term')).not.toBeInTheDocument(); // no <dt>
  });

  it('shows no tools section when tools list is empty', () => {
    const noToolsTemplate: Template = {
      ...TEMPLATE,
      spec: { ...TEMPLATE.spec, tools: [] },
    };
    render(<TemplateCard template={noToolsTemplate} onEdit={vi.fn()} onClone={vi.fn()} />);
    expect(screen.queryByText('bash')).not.toBeInTheDocument();
  });
});
