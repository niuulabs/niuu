import { BALANCED_TRUST, type TrustPreset } from './realm';

export interface RealmTemplate {
  id: string;
  name: string;
  blurb: string;
  covers: string[];
  needs: string[];
  needsRepo: boolean;
  needsBoard: boolean;
  needsBugBoard: boolean;
  recommended?: boolean;
  trust: TrustPreset;
}

/**
 * The ids are kept as stored on existing workspaces' observe grant (`limits.template`),
 * so they keep their old "-resident" suffix.
 */
export const REALM_TEMPLATES: RealmTemplate[] = [
  {
    id: 'product-resident',
    name: 'Product workspace',
    blurb:
      'A codebase end to end: its tracker board, sandboxed sessions that work the tickets into pull requests, and workspace memory. Gates stop at you before merging or deploying.',
    covers: [
      'a board for new tickets',
      'sessions that work tickets into pull requests',
      'review gates before anything merges',
      'workspace memory for what is learned',
    ],
    needs: ['repository', 'tracker board', 'CI', 'bug board (optional)'],
    needsRepo: true,
    needsBoard: true,
    needsBugBoard: false,
    recommended: true,
    trust: BALANCED_TRUST,
  },
  {
    id: 'qa-resident',
    name: 'QA workspace',
    blurb:
      'Not for features. Pull request review, bug reproduction, a bug board for findings and a regression list.',
    covers: [
      'a bug board for reported bugs',
      'sessions that review pull requests for regressions',
      'a regression list in workspace memory',
    ],
    needs: ['repository', 'bug board'],
    needsRepo: true,
    needsBoard: true,
    needsBugBoard: true,
    trust: { ...BALANCED_TRUST, deploy: 'never' },
  },
  {
    id: 'docs-resident',
    name: 'Docs workspace',
    blurb:
      'Docs, runbooks and workspace memory kept in step with the code. Sessions draft updates; production code stays untouched.',
    covers: ['sessions that compare code and docs', 'doc updates drafted as pull requests'],
    needs: ['repository', 'workspace memory'],
    needsRepo: true,
    needsBoard: false,
    needsBugBoard: false,
    trust: { ...BALANCED_TRUST, deploy: 'never', test: 'ask' },
  },
  {
    id: 'blank-resident',
    name: 'Blank workspace',
    blurb:
      'Only a charter. Good when the environment is not a codebase: a lab, a home, a shop floor.',
    covers: ['whatever the charter says'],
    needs: ['a charter'],
    needsRepo: false,
    needsBoard: false,
    needsBugBoard: false,
    trust: { ...BALANCED_TRUST, build: 'ask', test: 'ask', deploy: 'never' },
  },
];

export function templateById(id: string): RealmTemplate {
  const template = REALM_TEMPLATES.find((candidate) => candidate.id === id);
  if (!template) throw new Error(`Unknown workspace template: ${id}`);
  return template;
}
