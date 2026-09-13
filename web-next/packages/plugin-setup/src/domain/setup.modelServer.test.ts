import { describe, expect, it } from 'vitest';
import { createMockSetupService } from '../adapters/mock';
import { describeStagedChanges } from './setup';

describe('describeStagedChanges with a model server', () => {
  it('names the server and its model count, and says when it stops', async () => {
    const service = createMockSetupService({ latencyMs: 0 });
    const staged = await service.stageStack({
      vllm_enabled: false,
      model_server_enabled: true,
      model_server_url: 'http://host.docker.internal:11434',
      model_server_models: ['llama3.2:latest', 'qwen3:8b'],
    });
    expect(describeStagedChanges(staged)).toContain(
      'Use your model server at http://host.docker.internal:11434 (2 models)',
    );
    expect(staged.staged).toEqual({
      docker: {
        vllm: { enabled: false },
        model_server: {
          enabled: true,
          base_url: 'http://host.docker.internal:11434',
          models: ['llama3.2:latest', 'qwen3:8b'],
        },
      },
    });

    const off = createMockSetupService({
      latencyMs: 0,
      initialStack: {
        modelServer: { enabled: true, baseUrl: 'http://x:1', models: ['m'], hasApiKey: false },
      },
    });
    const stopped = await off.stageStack({ model_server_enabled: false });
    expect(describeStagedChanges(stopped)).toContain('Stop using your model server');
  });
});
