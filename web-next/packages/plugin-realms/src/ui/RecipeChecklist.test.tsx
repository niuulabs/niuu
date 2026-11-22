import { describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { ShellContext } from '@niuulabs/shell';
import { RECIPE_STEPS } from '../application/useCreateRealm';
import { WITHOUT_TING_PLUGIN } from '../testing/renderRealms';
import { RecipeChecklist } from './RecipeChecklist';

vi.mock('@tanstack/react-router', async (original) => ({
  ...(await original<typeof import('@tanstack/react-router')>()),
  Link: ({ children, to }: { children: ReactNode; to: string }) => <a href={to}>{children}</a>,
}));

function failed(stepId: string, tingPlugin: boolean) {
  render(
    <ServicesProvider services={{ ting: {}, 'ting.work': {} }}>
      <ShellContext.Provider
        value={
          tingPlugin
            ? null
            : {
                enabled: WITHOUT_TING_PLUGIN,
                brand: null,
                version: '0',
                ctx: { tweaks: {}, setTweak: () => {} },
              }
        }
      >
        <RecipeChecklist
          progress={{
            states: {},
            error: 'HTTP 500',
            failedStep: RECIPE_STEPS.find((step) => step.id === stepId)!,
            realm: null,
          }}
        />
      </ShellContext.Provider>
    </ServicesProvider>,
  );
  const alert = screen.getByRole('alert');
  expect(alert).toHaveTextContent('Nothing was undone.');
  return within(alert).queryByRole('link')?.getAttribute('href') ?? null;
}

describe('RecipeChecklist, at a failed step', () => {
  it.each([
    ['board', true, '/ting/work'],
    ['board', false, null],
    ['memory', false, '/mimir/registry'],
  ])('%s: points at its page when the host has it (Ting pages: %s)', (step, tingPlugin, href) => {
    expect(failed(step, tingPlugin)).toBe(href);
  });
});
