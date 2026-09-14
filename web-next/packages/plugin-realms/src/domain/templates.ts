import type { PersonaCreateRequest } from '@niuulabs/plugin-ravn';
import { BALANCED_TRUST, type TrustPreset } from './realm';

export interface TemplateJob {
  kind: 'cron' | 'event' | 'webhook';
  spec: string;
  label: string;
}

export interface RealmTemplate {
  id: string;
  name: string;
  blurb: string;
  keepsDoing: string[];
  needs: string[];
  needsBoard: boolean;
  needsBugBoard: boolean;
  recommended?: boolean;
  jobs: TemplateJob[];
  trust: TrustPreset;
  persona: Omit<PersonaCreateRequest, 'name' | 'summary' | 'description' | 'systemPromptTemplate'>;
  prompt: (charter: string, repo: string, board: string) => string;
}

const basePersona: RealmTemplate['persona'] = {
  role: 'build',
  letter: 'R',
  color: '',
  allowedTools: [],
  forbiddenTools: [],
  permissionMode: 'default',
  iterationBudget: 20,
  llmThinkingEnabled: false,
  llmMaxTokens: 8192,
  producesEventType: '',
  producesSchema: {},
  consumesEvents: [],
  consumesSchema: {},
  mimirWriteRouting: 'domain',
};

function prompt(role: string) {
  return (charter: string, repo: string, board: string) =>
    [
      `# ${role}`,
      '',
      'You are the resident that keeps this realm. Your charter, written by its owner:',
      '',
      charter.trim(),
      '',
      `Repository: ${repo || 'not set'}`,
      `Tracker board: ${board || 'not set'}`,
      '',
      'Work inside your trust grants. When an action asks for approval, ask and wait.',
      'Write what you learn to realm memory so the next turn starts from it.',
    ].join('\n');
}

export const REALM_TEMPLATES: RealmTemplate[] = [
  {
    id: 'product-resident',
    name: 'Product resident',
    blurb:
      'Owns a codebase end to end. Pulls tickets from your tracker, works them in sandboxed sessions, opens pull requests, runs the QA loop on every change and watches health. Asks you before merging or deploying.',
    keepsDoing: [
      'intake and triage new tickets',
      'work tickets → pull requests',
      'validate every PR against acceptance criteria',
      'watch CI, errors and staleness',
      'write what it learns to realm memory',
    ],
    needs: ['repository', 'tracker board', 'CI', 'bug board (optional)'],
    needsBoard: true,
    needsBugBoard: false,
    recommended: true,
    jobs: [
      { kind: 'cron', spec: '*/15 * * * *', label: 'intake every 15 min' },
      { kind: 'event', spec: 'github.pull_request.opened', label: 'QA loop on every pull request' },
      { kind: 'cron', spec: '0 2 * * *', label: 'nightly health sweep' },
    ],
    trust: BALANCED_TRUST,
    persona: basePersona,
    prompt: prompt('Product resident'),
  },
  {
    id: 'qa-resident',
    name: 'QA resident',
    blurb:
      'Does not write features. Reviews every pull request, reproduces reported bugs, files and updates issues in your bug board, keeps a regression list.',
    keepsDoing: [
      'reproduce and confirm bug reports',
      'review pull requests for regressions',
      'keep the regression suite green',
    ],
    needs: ['repository', 'bug board'],
    needsBoard: true,
    needsBugBoard: true,
    jobs: [
      { kind: 'event', spec: 'github.pull_request.opened', label: 'review every pull request' },
      { kind: 'cron', spec: '0 */2 * * *', label: 'check the bug board every 2 h' },
    ],
    trust: { ...BALANCED_TRUST, deploy: 'never' },
    persona: basePersona,
    prompt: prompt('QA resident'),
  },
  {
    id: 'docs-resident',
    name: 'Docs resident',
    blurb:
      'Keeps docs, runbooks and realm memory in step with the code. Flags stale pages, drafts updates, never touches production code.',
    keepsDoing: ['detect drift between code and docs', 'draft doc updates as pull requests'],
    needs: ['repository', 'realm memory'],
    needsBoard: false,
    needsBugBoard: false,
    jobs: [{ kind: 'cron', spec: '0 3 * * *', label: 'nightly drift check' }],
    trust: { ...BALANCED_TRUST, deploy: 'never', test: 'ask' },
    persona: basePersona,
    prompt: prompt('Docs resident'),
  },
  {
    id: 'blank-resident',
    name: 'Blank resident',
    blurb:
      'Only a charter. Good when the environment is not a codebase: a lab, a home, a shop floor.',
    keepsDoing: ['whatever the charter says'],
    needs: ['a charter'],
    needsBoard: false,
    needsBugBoard: false,
    jobs: [],
    trust: { ...BALANCED_TRUST, build: 'ask', test: 'ask', deploy: 'never' },
    persona: basePersona,
    prompt: prompt('Resident'),
  },
];

export function templateById(id: string): RealmTemplate {
  const template = REALM_TEMPLATES.find((candidate) => candidate.id === id);
  if (!template) throw new Error(`Unknown realm template: ${id}`);
  return template;
}
