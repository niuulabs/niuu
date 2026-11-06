import { describe, expect, it } from 'vitest';
import { SECRET_REQUEST_TOOL_NAME, parseSecretRequestInput, secretTitle } from './secret-request';

describe('parseSecretRequestInput', () => {
  it('reads the request a secret_request part describes', () => {
    expect(SECRET_REQUEST_TOOL_NAME).toBe('secret_request');
    expect(
      parseSecretRequestInput({
        request_id: 'r-1',
        name: 'GITLAB_TOKEN',
        purpose: ' Push the branch ',
        target: 'gitlab.niuu.world',
      }),
    ).toEqual({
      requestId: 'r-1',
      name: 'GITLAB_TOKEN',
      purpose: 'Push the branch',
      target: 'gitlab.niuu.world',
    });
  });

  it('treats a missing or blank purpose and target as empty', () => {
    expect(parseSecretRequestInput({ request_id: 'r-1', name: 'X', target: '  ' })).toEqual({
      requestId: 'r-1',
      name: 'X',
      purpose: '',
      target: null,
    });
  });

  it.each([
    null,
    'text',
    42,
    {},
    { request_id: 'r-1' },
    { name: 'X' },
    { request_id: 1, name: 'X' },
  ])('rejects an unusable input %j', (input) => {
    expect(parseSecretRequestInput(input)).toBeNull();
  });
});

describe('secretTitle', () => {
  it('turns the variable name into words', () => {
    expect(secretTitle('GITLAB_TOKEN')).toBe('Gitlab token');
    expect(secretTitle('_API__KEY')).toBe('Api key');
    expect(secretTitle('')).toBe('');
  });
});
