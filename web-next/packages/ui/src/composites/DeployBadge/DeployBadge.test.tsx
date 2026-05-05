import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { DeployBadge, DEPLOY_GLYPH } from './DeployBadge';
import type { DeploymentKind } from './DeployBadge';

const ALL_DEPLOYMENTS: DeploymentKind[] = ['k8s', 'systemd', 'pi', 'mobile', 'ephemeral'];

describe('DeployBadge', () => {
  it.each(ALL_DEPLOYMENTS)('renders the correct glyph for "%s"', (deployment) => {
    render(<DeployBadge deployment={deployment} />);
    expect(screen.getByText(DEPLOY_GLYPH[deployment])).toBeInTheDocument();
  });

  it.each(ALL_DEPLOYMENTS)('renders the deployment label for "%s"', (deployment) => {
    render(<DeployBadge deployment={deployment} />);
    expect(screen.getByText(deployment)).toBeInTheDocument();
  });

  it.each(ALL_DEPLOYMENTS)('has the correct aria-label for "%s"', (deployment) => {
    render(<DeployBadge deployment={deployment} />);
    expect(screen.getByLabelText(deployment)).toBeInTheDocument();
  });

  it('applies the deployment modifier class', () => {
    const { container } = render(<DeployBadge deployment="k8s" />);
    expect(container.firstChild).toHaveClass('niuu-deploy-badge--k8s');
  });

  it('accepts a custom title', () => {
    render(<DeployBadge deployment="k8s" title="kubernetes cluster" />);
    expect(screen.getByTitle('kubernetes cluster')).toBeInTheDocument();
    expect(screen.getByLabelText('kubernetes cluster')).toBeInTheDocument();
  });

  it('accepts a custom className', () => {
    const { container } = render(<DeployBadge deployment="pi" className="extra" />);
    expect(container.firstChild).toHaveClass('niuu-deploy-badge', 'extra');
  });

  it('DEPLOY_GLYPH covers all 5 deployment kinds', () => {
    expect(Object.keys(DEPLOY_GLYPH)).toHaveLength(5);
    for (const kind of ALL_DEPLOYMENTS) {
      expect(DEPLOY_GLYPH[kind]).toBeTruthy();
    }
  });
});
