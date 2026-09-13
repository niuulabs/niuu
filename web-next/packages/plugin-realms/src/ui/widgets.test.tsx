import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { RECIPE_STEPS } from '../application/useCreateRealm';
import { FIRST_REALM_WALKTHROUGH } from '../application/useWalkthrough';
import { RecipeChecklist } from './RecipeChecklist';
import { WalkthroughRail } from './WalkthroughRail';
import { renderRealms } from '../testing/renderRealms';

describe('RecipeChecklist', () => {
  afterEach(cleanup);

  it('renders every step with its state', () => {
    const states = Object.fromEntries(RECIPE_STEPS.map((step) => [step.id, 'todo' as const]));
    states.validate = 'done';
    states.connections = 'running';
    render(<RecipeChecklist progress={{ states, error: null, failedStep: null, realm: null }} />);
    expect(screen.getByTestId('recipe-step-validate')).toHaveAttribute('data-state', 'done');
    expect(screen.getByTestId('recipe-step-connections')).toHaveAttribute('data-state', 'running');
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });
});

describe('WalkthroughRail', () => {
  afterEach(() => {
    cleanup();
    localStorage.clear();
  });

  it('shows progress, the current step detail and can be hidden', async () => {
    const user = userEvent.setup();
    render(<WalkthroughRail walkthrough={FIRST_REALM_WALKTHROUGH} action={<span>act</span>} />);
    expect(screen.getByTestId('walkthrough-rail')).toHaveTextContent('0 / 5');
    expect(screen.getByText('Product resident fits most codebases.')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Hide walkthrough' }));
    expect(screen.queryByTestId('walkthrough-rail')).not.toBeInTheDocument();
  });
});

describe('templates route', () => {
  afterEach(cleanup);

  it('routes a template pick into the wizard', async () => {
    const user = userEvent.setup();
    const { router } = renderRealms('/realms/templates');
    await user.click((await screen.findAllByText('Use this template'))[1]!);
    expect(router.state.location.pathname).toBe('/realms/new');
    expect(router.state.location.search).toMatchObject({ template: 'qa-resident' });
  });
});
