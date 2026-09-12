import { describe, expect, it } from 'vitest';
import { MOCK_CATALOG, MOCK_SYSTEM } from '../adapters/mock';
import {
  connectionNeedsSignIn,
  accessMode,
  accessUrls,
  enrollmentFailureMessage,
  isEnrollmentActive,
  needsInteractiveSignIn,
  WIZARD_STEPS,
  backendStepId,
  buildConfigPayload,
  catalogForStep,
  connectionForSlug,
  credentialNameFor,
  formatGib,
  formatGpu,
  hostChips,
  hostFlavor,
  isConnectableFromWizard,
  isStepDone,
  missingCredentialKeys,
  nextStep,
  previousStep,
  requiredCredentialKeys,
  stepIndex,
  type CatalogEntry,
  type SetupState,
} from './setup';

const github = MOCK_CATALOG.find((entry) => entry.slug === 'github')!;
const claudeCode = MOCK_CATALOG.find((entry) => entry.slug === 'claude-code')!;

function state(steps: string[]): SetupState {
  return {
    enabled: true,
    mode: 'docker',
    completed: false,
    completedAt: null,
    steps: [],
    completedSteps: steps.map((step) => ({ step, completedAt: '2026-09-12T10:00:00Z', data: {} })),
  };
}

describe('wizard steps', () => {
  it('maps finish to the backend launch step', () => {
    expect(backendStepId('finish')).toBe('launch');
    expect(backendStepId('git')).toBe('git');
  });

  it('walks forward and backward', () => {
    expect(stepIndex('welcome')).toBe(0);
    expect(nextStep('welcome')).toBe('system');
    expect(nextStep('finish')).toBeNull();
    expect(previousStep('welcome')).toBeNull();
    expect(previousStep('system')).toBe('welcome');
    expect(WIZARD_STEPS.at(-1)?.id).toBe('finish');
  });

  it('reads completion from the backend records', () => {
    expect(isStepDone(undefined, 'system')).toBe(false);
    expect(isStepDone(state(['system', 'launch']), 'system')).toBe(true);
    expect(isStepDone(state(['launch']), 'finish')).toBe(true);
    expect(isStepDone(state([]), 'git')).toBe(false);
  });
});

describe('catalog helpers', () => {
  it('filters entries per step and skips non-integration steps', () => {
    const providers = WIZARD_STEPS.find((step) => step.id === 'providers')!;
    expect(catalogForStep(MOCK_CATALOG, providers).map((e) => e.slug)).toEqual([
      'anthropic',
      'openai',
      'claude-code',
      'codex',
      'xai',
    ]);
    expect(catalogForStep(MOCK_CATALOG, WIZARD_STEPS[0]!)).toEqual([]);
  });

  it('knows which entries the wizard can connect', () => {
    expect(isConnectableFromWizard(github)).toBe(true);
    expect(isConnectableFromWizard(claudeCode)).toBe(false);
    expect(isConnectableFromWizard({ ...github, authType: 'device_code' })).toBe(false);
  });

  it('finds enabled connections by slug', () => {
    const connections = [
      {
        id: '1',
        slug: 'github',
        integrationType: 'source_control',
        credentialName: 'c',
        enabled: false,
        config: {},
        credentialStatus: 'valid',
      },
      {
        id: '2',
        slug: 'github',
        integrationType: 'source_control',
        credentialName: 'c',
        enabled: true,
        config: {},
        credentialStatus: 'valid',
      },
    ];
    expect(connectionForSlug(connections, 'github')?.id).toBe('2');
    expect(connectionForSlug(connections, 'linear')).toBeUndefined();
  });

  it('derives required credential keys, falling back to properties', () => {
    expect(requiredCredentialKeys(github)).toEqual(['token']);
    const noRequired: CatalogEntry = {
      ...github,
      credentialSchema: { properties: { a: { label: 'A', type: 'password' } } },
    };
    expect(requiredCredentialKeys(noRequired)).toEqual(['a']);
    expect(missingCredentialKeys(github, {})).toEqual(['token']);
    expect(missingCredentialKeys(github, { token: '  ' })).toEqual(['token']);
    expect(missingCredentialKeys(github, { token: 'x' })).toEqual([]);
  });

  it('builds config payloads with defaults and lists', () => {
    expect(buildConfigPayload(github, { orgs: 'a, b ,, c', name: '' })).toEqual({
      base_url: 'https://api.github.com',
      orgs: ['a', 'b', 'c'],
    });
    expect(buildConfigPayload(github, { name: 'Work', base_url: 'https://ghe.example' })).toEqual({
      name: 'Work',
      base_url: 'https://ghe.example',
    });
    expect(buildConfigPayload(claudeCode, {})).toEqual({});
  });

  it('normalises credential names', () => {
    expect(credentialNameFor('GitHub')).toBe('github-setup');
    expect(credentialNameFor('claude code!')).toBe('claude-code--setup');
  });
});

describe('host presentation', () => {
  it('formats sizes', () => {
    expect(formatGib(0)).toBe('0 GiB');
    expect(formatGib(Number.NaN)).toBe('0 GiB');
    expect(formatGib(128 * 1024 ** 3)).toBe('128 GiB');
    expect(formatGpu({ name: 'GB10', memory_total_mib: 131072, driver_version: '1' })).toBe(
      'GB10 · 128 GiB',
    );
    expect(formatGpu({ name: 'X', memory_total_mib: 0, driver_version: '1' })).toBe('X');
  });

  it('names the host flavor from the GPU', () => {
    expect(hostFlavor(null)).toBe('this machine');
    expect(hostFlavor(MOCK_SYSTEM.host)).toBe('this DGX Spark');
    expect(
      hostFlavor({
        ...MOCK_SYSTEM.host!,
        gpus: [{ name: 'RTX 5090', memory_total_mib: 32768, driver_version: '1' }],
      }),
    ).toBe('this GPU host');
    expect(hostFlavor({ ...MOCK_SYSTEM.host!, gpus: [] })).toBe('this machine');
  });

  it('builds chips from facts', () => {
    expect(hostChips(null)).toEqual([]);
    const chips = hostChips(MOCK_SYSTEM.host).map((chip) => chip.label);
    expect(chips[0]).toBe('spark');
    expect(chips).toContain('128 GiB memory');
    expect(chips).toContain('NVIDIA GB10 · 128 GiB');
    expect(chips).toContain('Docker 27.3.1');
    const bare = hostChips({ ...MOCK_SYSTEM.host!, memory_total_bytes: 0, docker_version: '' });
    expect(bare.map((c) => c.label)).not.toContain('Docker ');
  });
});

describe('interactive sign-in helpers', () => {
  const enrollment = {
    id: 'e',
    connectionId: 'c',
    providerSlug: 'codex',
    credentialName: 'n',
    state: 'failed' as const,
    verificationUri: '',
    userCode: '',
    expiresAt: '',
    errorCode: '',
    inputRequired: false,
  };

  it('knows which catalog entries sign in interactively', () => {
    expect(needsInteractiveSignIn({ ...MOCK_CATALOG[0]!, authType: 'browser_login' })).toBe(true);
    expect(needsInteractiveSignIn({ ...MOCK_CATALOG[0]!, authType: 'device_code' })).toBe(true);
    expect(needsInteractiveSignIn(MOCK_CATALOG[0]!)).toBe(false);
  });

  it('tracks active enrollments', () => {
    expect(isEnrollmentActive(undefined)).toBe(false);
    expect(isEnrollmentActive({ ...enrollment, state: 'pending' })).toBe(true);
    expect(isEnrollmentActive({ ...enrollment, state: 'awaiting_user' })).toBe(true);
    expect(isEnrollmentActive({ ...enrollment, state: 'complete' })).toBe(false);
  });

  it('explains failures in plain words', () => {
    expect(enrollmentFailureMessage({ ...enrollment, state: 'expired' })).toMatch(/timed out/);
    expect(enrollmentFailureMessage({ ...enrollment, state: 'cancelled' })).toBe(
      'Sign-in cancelled.',
    );
    expect(enrollmentFailureMessage({ ...enrollment, errorCode: 'runner_start_failed' })).toMatch(
      /could not be started/,
    );
    expect(enrollmentFailureMessage({ ...enrollment, errorCode: 'login_worker_missing' })).toMatch(
      /could not be started/,
    );
    expect(
      enrollmentFailureMessage({ ...enrollment, errorCode: 'provider_login_rejected' }),
    ).toMatch(/rejected/);
    expect(
      enrollmentFailureMessage({ ...enrollment, errorCode: 'claude_token_not_found' }),
    ).toMatch(/without handing back/);
    expect(enrollmentFailureMessage({ ...enrollment, errorCode: 'unexpected_login_url' })).toMatch(
      /unexpected address/,
    );
    expect(enrollmentFailureMessage({ ...enrollment, errorCode: 'other' })).toBe(
      'Sign-in failed (other).',
    );
    expect(enrollmentFailureMessage(enrollment)).toBe('Sign-in failed.');
  });
});

describe('access presentation', () => {
  it('derives the access mode from the bind address', () => {
    expect(accessMode(null)).toBe('unknown');
    expect(accessMode({ ...MOCK_SYSTEM.host!, bind_host: '' })).toBe('unknown');
    expect(accessMode({ ...MOCK_SYSTEM.host!, bind_host: '127.0.0.1' })).toBe('local');
    expect(accessMode({ ...MOCK_SYSTEM.host!, bind_host: 'localhost' })).toBe('local');
    expect(accessMode({ ...MOCK_SYSTEM.host!, bind_host: '0.0.0.0' })).toBe('lan');
  });

  it('lists the addresses the web app answers on', () => {
    expect(accessUrls(null)).toEqual([]);
    expect(accessUrls({ ...MOCK_SYSTEM.host!, port: 0 })).toEqual([]);
    expect(accessUrls({ ...MOCK_SYSTEM.host!, bind_host: '127.0.0.1' })).toEqual([
      'http://127.0.0.1:8080',
    ]);
    expect(accessUrls(MOCK_SYSTEM.host)).toEqual([
      'http://127.0.0.1:8080',
      'http://192.168.1.42:8080',
    ]);
    expect(accessUrls({ ...MOCK_SYSTEM.host!, external_host: '' })).toEqual([
      'http://127.0.0.1:8080',
    ]);
  });
});

describe('connection credential state', () => {
  const connection = {
    id: 'c',
    slug: 'claude-code',
    integrationType: 'ai_provider',
    credentialName: 'n',
    enabled: true,
    config: {},
    credentialStatus: 'active',
  };
  it('knows when a connection still needs a sign-in', () => {
    expect(connectionNeedsSignIn(connection)).toBe(false);
    expect(connectionNeedsSignIn({ ...connection, credentialStatus: 'auth_required' })).toBe(true);
    expect(connectionNeedsSignIn({ ...connection, credentialStatus: 'enrolling' })).toBe(true);
  });
});
