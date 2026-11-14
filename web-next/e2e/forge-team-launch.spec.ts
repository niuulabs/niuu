import { test, expect, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';

const baseConfig = JSON.parse(
  readFileSync(new URL('../apps/niuu/public/config.json', import.meta.url), 'utf8'),
);
const origin = 'http://forge-team-fixture.invalid';
const repoUrl = 'https://example.test/niuu.git';

interface Call {
  method: string;
  path: string;
  search: URLSearchParams;
  body: Record<string, unknown>;
}

interface FixtureOptions {
  /** Answer `POST /teams` with this status and detail instead of starting the team. */
  failStart?: { status: number; detail: string };
  /** Hold the engine list until this resolves, to see the page while it loads. */
  engines?: Promise<void>;
}

const hosts = [
  {
    id: 'thor',
    slug: 'local',
    name: 'Thor',
    baseUrl: 'http://100.66.123.128:8080',
    enabled: true,
    isDefault: true,
    config: {},
    tags: [],
  },
  {
    id: 'spark',
    slug: 'spark',
    name: 'Spark',
    baseUrl: 'http://100.127.141.74:8080',
    enabled: true,
    isDefault: false,
    config: {},
    tags: [],
  },
];

const people = [
  { user_id: 'u-alice', display_name: 'Alice Lindqvist', email: 'alice@example.test' },
  { user_id: 'u-bram', display_name: 'Bram Koster', email: 'bram@example.test' },
];

/** A Forge that can start teams: records every call, so a test can read what was sent. */
async function fixture(page: Page, options: FixtureOptions = {}) {
  const calls: Call[] = [];
  // An already configured installation: the first-run wizard stays out of the way.
  await page.route('**/api/v1/niuu/setup', (route) =>
    route.fulfill({ json: { enabled: false, completed: true, steps: [], completedSteps: [] } }),
  );
  await page.route(/\/config(?:\.live)?\.json$/, (route) =>
    route.fulfill({
      json: {
        ...baseConfig,
        theme: 'xteo',
        services: {
          ...baseConfig.services,
          setup: { mode: 'http', baseUrl: '/api/v1/niuu/setup' },
          forge: { mode: 'http', baseUrl: `${origin}/api/v1/forge` },
          volundr: { mode: 'http', baseUrl: `${origin}/api/v1/volundr` },
          niuu: { mode: 'http', baseUrl: `${origin}/api/v1/niuu` },
          bifrost: { mode: 'http', baseUrl: `${origin}/api/v1/bifrost` },
        },
      },
    }),
  );
  await page.routeWebSocket('ws://forge-team-fixture.invalid/**', () => {});
  await page.route(`${origin}/**`, async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const method = route.request().method();
    calls.push({
      method,
      path,
      search: url.searchParams,
      body: method === 'GET' ? {} : (route.request().postDataJSON() ?? {}),
    });
    if (method === 'POST' && path.endsWith('/teams')) {
      if (options.failStart) {
        return route.fulfill({
          status: options.failStart.status,
          json: { detail: options.failStart.detail },
        });
      }
      const body = route.request().postDataJSON() as { name: string };
      return route.fulfill({
        status: 201,
        json: {
          session: {
            id: 'team-root',
            name: body.name,
            status: 'starting',
            model: 'claude-fable-5-1',
            team: { task_to: null },
            people_count: 1,
          },
          members: [],
        },
      });
    }
    if (method !== 'GET') return route.fulfill({ status: 400, json: { detail: 'Unexpected' } });
    if (path.endsWith('/instances')) return route.fulfill({ json: hosts });
    if (path.endsWith('/participant-candidates')) {
      const typed = (url.searchParams.get('q') ?? '').toLowerCase();
      return route.fulfill({
        json: people.filter(
          (person) =>
            person.display_name.toLowerCase().includes(typed) ||
            person.email.toLowerCase().includes(typed),
        ),
      });
    }
    if (path.endsWith('/session-definitions')) {
      await options.engines;
      return route.fulfill({
        json: [
          { key: 'skuldClaudeInteractive', displayName: 'Claude Code', compatibleProviders: [] },
          { key: 'skuldCodex', displayName: 'Codex', compatibleProviders: [] },
        ].map((definition) => ({ labels: [], description: '', defaultModel: '', ...definition })),
      });
    }
    if (path.endsWith('/integrations/catalog'))
      return route.fulfill({
        json: [
          {
            id: 'cat-claude',
            slug: 'claude-code',
            name: 'Claude',
            description: '',
            integration_type: 'ai_provider',
            model_vendor: 'anthropic',
          },
          {
            id: 'cat-openai',
            slug: 'openai',
            name: 'OpenAI',
            description: '',
            integration_type: 'ai_provider',
            model_vendor: 'openai',
          },
          {
            id: 'cat-server',
            slug: 'model-server',
            name: 'Model server',
            description: '',
            integration_type: 'ai_provider',
            model_vendor: 'local',
          },
        ],
      });
    if (path.endsWith('/integrations'))
      return route.fulfill({
        json: [
          {
            id: 'conn-claude',
            slug: 'claude-code',
            integration_type: 'ai_provider',
            enabled: true,
          },
          { id: 'conn-openai', slug: 'openai', integration_type: 'ai_provider', enabled: true },
          {
            id: 'conn-server',
            slug: 'model-server',
            integration_type: 'ai_provider',
            enabled: true,
            config: { models: ['llama3.2:latest', 'qwen3:8b'] },
          },
        ],
      });
    if (path.endsWith('/repos'))
      return route.fulfill({
        json: [
          {
            id: 'niuu',
            name: 'niuu',
            org: 'niuu',
            url: repoUrl,
            cloneUrl: repoUrl,
            defaultBranch: 'main',
            branches: ['main'],
          },
        ],
      });
    if (path.endsWith('/branches')) return route.fulfill({ json: ['main'] });
    if (path.endsWith('/members')) return route.fulfill({ json: { agents: [], people: [] } });
    if (path.endsWith('/stats')) return route.fulfill({ json: {} });
    if (path.endsWith('/stream'))
      return route.fulfill({ contentType: 'text/event-stream', body: '' });
    return route.fulfill({ json: [] });
  });
  return calls;
}

const teamCalls = (calls: Call[]) =>
  calls.filter((call) => call.method === 'POST' && call.path.endsWith('/teams'));

async function openTeamTab(page: Page) {
  await page.goto(`/volundr/sessions/new?repo=${encodeURIComponent(repoUrl)}&branch=main`);
  await page.getByRole('button', { name: 'A team', exact: true }).click();
  await expect(page.getByTestId('new-team-members')).toBeVisible();
}

test('a team starts with a task for the lead, a person invited, on the selected Forge', async ({
  page,
}) => {
  const calls = await fixture(page);
  await openTeamTab(page);

  await expect(page.getByTestId('new-session-start')).toHaveText('Start 2 sessions');
  await page.getByTestId('new-session-task').fill('Token refresh fails after 60 minutes.');
  await expect(page.getByTestId('new-team-task-to-lead')).toHaveAttribute('aria-checked', 'true');
  await expect(page.getByTestId('new-team-room')).toContainText('gets the task');

  await page.getByTestId('new-team-add-person').click();
  await page.getByLabel('Find someone in your org').fill('ali');
  await page.getByRole('button', { name: /Alice Lindqvist/ }).click();
  await expect(page.getByTestId('new-team-person-u-alice')).toContainText(
    'joins the room · can message anyone',
  );
  // The people are searched on the Forge the team will start on.
  const search = calls.find((call) => call.path.endsWith('/participant-candidates'));
  expect(search?.search.get('q')).toBe('ali');
  expect(search?.search.get('instance_id')).toBe('thor');

  await expect(page.getByTestId('new-session-start')).toBeEnabled();
  await page.getByTestId('new-session-start').click();
  await expect.poll(() => teamCalls(calls).length).toBe(1);
  const body = teamCalls(calls)[0]!.body;
  expect(body).toMatchObject({
    name: 'niuu',
    source: { type: 'git', repo: repoUrl, branch: 'main' },
    task: 'Token refresh fails after 60 minutes.',
    task_to: 'lead',
    invitees: ['u-alice'],
    instance_id: 'thor',
  });
  const members = body.members as Array<{ lead: boolean }>;
  expect(members).toHaveLength(2);
  expect(members.filter((member) => member.lead)).toHaveLength(1);
  await expect(page).toHaveURL(/\/volundr\/sessions\/team-root/);
});

test('a team with no task waits in its room: nobody is asked who gets it', async ({ page }) => {
  const calls = await fixture(page);
  await openTeamTab(page);

  await expect(page.getByTestId('new-team-task-to')).toHaveCount(0);
  await expect(page.getByTestId('new-team-room')).toContainText('All 2 agents start and wait');
  await expect(page.getByTestId('new-session-start')).toBeEnabled();
  await page.getByTestId('new-session-start').click();

  await expect.poll(() => teamCalls(calls).length).toBe(1);
  const body = teamCalls(calls)[0]!.body;
  expect(body).not.toHaveProperty('task_to');
  expect(body.task).toBe('');
  expect(body).not.toHaveProperty('invitees');
});

test('the task can go to everyone', async ({ page }) => {
  const calls = await fixture(page);
  await openTeamTab(page);

  await page.getByTestId('new-session-task').fill('Review the migration.');
  await page.getByTestId('new-team-task-to-everyone').click();
  await expect(page.getByTestId('new-team-room')).toContainText('All 2 agents get the task');
  await page.getByTestId('new-session-start').click();

  await expect.poll(() => teamCalls(calls).length).toBe(1);
  expect(teamCalls(calls)[0]!.body).toMatchObject({ task_to: 'everyone' });
});

test('the page waits for the engines before a team can start', async ({ page }) => {
  let release: () => void = () => {};
  const engines = new Promise<void>((resolve) => (release = resolve));
  await fixture(page, { engines });
  await openTeamTab(page);

  await expect(page.getByText('Loading engines…')).toBeVisible();
  await expect(page.getByTestId('new-session-start')).toBeDisabled();
  release();
  await expect(page.getByTestId('new-session-start')).toBeEnabled();
});

test('a team the Forge cannot start says why and stays on the page', async ({ page }) => {
  const calls = await fixture(page, {
    failStart: { status: 503, detail: 'This runtime cannot host the team room' },
  });
  await openTeamTab(page);

  await expect(page.getByTestId('new-session-start')).toBeEnabled();
  await page.getByTestId('new-session-start').click();
  await expect(page.getByTestId('new-session-error')).toContainText(
    'This runtime cannot host the team room',
  );
  expect(teamCalls(calls)).toHaveLength(1);
  await expect(page).toHaveURL(/\/volundr\/sessions\/new/);
});

test('who leads can be changed from the keyboard', async ({ page }) => {
  await fixture(page);
  await openTeamTab(page);

  const second = page.getByTestId('new-team-lead-agent-2');
  await expect(second).toHaveText('Make lead');
  await second.focus();
  await page.keyboard.press('Space');
  await expect(second).toHaveText('Leads');
  await expect(page.getByTestId('new-team-lead-agent-1')).toHaveText('Make lead');
});

test('each agent can run on its own provider and model', async ({ page }) => {
  const calls = await fixture(page);
  await openTeamTab(page);

  // The first agent keeps the engine's first provider; the second runs on the model server.
  await expect(page.getByTestId('new-team-engine-agent-1-account')).toHaveValue('conn-claude');
  await expect(page.getByTestId('new-team-engine-agent-1-model')).toHaveCount(0);
  await page.getByTestId('new-team-engine-agent-2-account').selectOption('conn-server');
  // A model server serves its own models: its first is picked until another is chosen.
  await expect(page.getByTestId('new-team-engine-agent-2-model')).toHaveValue('llama3.2:latest');
  await page.getByLabel('Model of agent 2').selectOption('qwen3:8b');

  // Another engine has its own providers: the row starts over on the first.
  await page.getByTestId('new-team-engine-agent-2').selectOption('skuldCodex');
  await expect(page.getByTestId('new-team-engine-agent-2-account')).toHaveValue('conn-claude');
  await expect(page.getByTestId('new-team-engine-agent-2-model')).toHaveCount(0);
  await page.getByTestId('new-team-engine-agent-2-account').selectOption('conn-server');
  await page.getByLabel('Model of agent 2').selectOption('qwen3:8b');

  await page.getByTestId('new-session-start').click();
  await expect.poll(() => teamCalls(calls).length).toBe(1);
  const body = teamCalls(calls)[0]!.body;
  const members = body.members as Array<{ model: string; definition: string }>;
  expect(members[0]).toMatchObject({ definition: 'skuldClaudeInteractive', model: '' });
  expect(members[1]).toMatchObject({ definition: 'skuldCodex', model: 'qwen3:8b' });
  // The union of the chosen providers' connections goes with the team.
  expect(body.integration_ids).toEqual(expect.arrayContaining(['conn-claude', 'conn-server']));
  expect(body.integration_ids).not.toContain('conn-openai');
});
