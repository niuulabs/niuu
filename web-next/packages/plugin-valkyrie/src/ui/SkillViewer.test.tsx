import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { wrapWithValkyrie } from '../testing/wrapWithValkyrie';
import { SkillNameButton, SkillViewer } from './SkillViewer';

describe('SkillViewer', () => {
  it('renders nothing while no skill is selected', () => {
    render(<SkillViewer environmentId="env-k8s-valhalla" skillName={null} onClose={() => {}} />, {
      wrapper: wrapWithValkyrie(),
    });
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('shows description, adoption info, requirements, markdown, and both code blocks', async () => {
    render(
      <SkillViewer
        environmentId="env-k8s-valhalla"
        skillName="k8s_memory_pressure_probe"
        onClose={() => {}}
      />,
      { wrapper: wrapWithValkyrie() },
    );

    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveTextContent('k8s_memory_pressure_probe');
    await waitFor(() => expect(dialog).toHaveTextContent('Read-only probe that inspects'));
    expect(dialog).toHaveTextContent('Adopted');
    expect(dialog).toHaveTextContent('valkyrie-valhalla-runa');
    expect(dialog).toHaveTextContent('kubernetes>=29.0.0');
    // skill markdown
    expect(screen.getByTestId('skill-viewer-content')).toHaveTextContent(
      'Detects and inspects Kubernetes Pod OOMKilled events',
    );
    // tool + test code in the app's code viewer
    expect(screen.getByTestId('skill-tool-code')).toHaveTextContent('def run(signal: dict)');
    expect(screen.getByTestId('skill-test-code')).toHaveTextContent(
      'def test_matches_oomkilled_pod',
    );
  });

  it('hides the code sections for a skill without code', async () => {
    render(
      <SkillViewer
        environmentId="env-k8s-valhalla"
        skillName="registry_token_refresh_check"
        onClose={() => {}}
      />,
      { wrapper: wrapWithValkyrie() },
    );

    await waitFor(() =>
      expect(screen.getByRole('dialog')).toHaveTextContent('pull-secret freshness'),
    );
    expect(screen.queryByTestId('skill-tool-code')).toBeNull();
    expect(screen.queryByTestId('skill-test-code')).toBeNull();
    expect(screen.getByRole('dialog')).not.toHaveTextContent('requirements');
  });

  it('explains a rolled-back skill calmly instead of showing an empty drawer (404)', async () => {
    render(
      <SkillViewer environmentId="env-k8s-valhalla" skillName="ghost_probe" onClose={() => {}} />,
      { wrapper: wrapWithValkyrie() },
    );

    const missing = await screen.findByTestId('skill-viewer-missing');
    expect(missing).toHaveTextContent('no longer installed on env-k8s-valhalla');
    expect(missing).toHaveTextContent('rolled back or retired');
    // A missing skill is an expected state, not an error.
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('surfaces load failures', async () => {
    const broken = {
      listSkills: () => Promise.reject(new Error('skills offline')),
      getSkill: () => Promise.reject(new Error('skill offline')),
    };
    render(
      <SkillViewer environmentId="env-k8s-valhalla" skillName="oom_probe" onClose={() => {}} />,
      { wrapper: wrapWithValkyrie({ 'valkyrie.skills': broken }) },
    );

    expect(await screen.findByRole('alert')).toHaveTextContent('skill offline');
  });

  it('calls onClose when the drawer is dismissed', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(
      <SkillViewer
        environmentId="env-k8s-valhalla"
        skillName="k8s_memory_pressure_probe"
        onClose={onClose}
      />,
      { wrapper: wrapWithValkyrie() },
    );

    await screen.findByRole('dialog');
    await user.click(screen.getByRole('button', { name: 'Close' }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

describe('SkillNameButton', () => {
  it('renders the skill name and reports clicks', async () => {
    const user = userEvent.setup();
    const onOpen = vi.fn();
    render(<SkillNameButton name="oom_probe" onOpen={onOpen} />);

    const button = screen.getByTestId('skill-link-oom_probe');
    expect(button).toHaveTextContent('oom_probe');
    expect(button).toHaveAccessibleName('View learned skill oom_probe');

    await user.click(button);
    expect(onOpen).toHaveBeenCalledWith('oom_probe');
  });
});
