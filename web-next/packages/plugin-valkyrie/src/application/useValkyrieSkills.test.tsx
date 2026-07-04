import { renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { wrapWithValkyrie } from '../testing/wrapWithValkyrie';
import { useValkyrieSkill, useValkyrieSkills } from './useValkyrieSkills';

describe('useValkyrieSkills', () => {
  it('lists learned skills for an environment through the injected service', async () => {
    const { result } = renderHook(() => useValkyrieSkills('env-k8s-valhalla'), {
      wrapper: wrapWithValkyrie(),
    });

    expect(result.current.isLoading).toBe(true);
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.map((skill) => skill.skillName)).toContain(
      'k8s_memory_pressure_probe',
    );
  });

  it('stays idle without an environment id or when disabled', () => {
    const empty = renderHook(() => useValkyrieSkills(''), { wrapper: wrapWithValkyrie() });
    expect(empty.result.current.fetchStatus).toBe('idle');

    const disabled = renderHook(() => useValkyrieSkills('env-k8s-valhalla', false), {
      wrapper: wrapWithValkyrie(),
    });
    expect(disabled.result.current.fetchStatus).toBe('idle');
  });

  it('surfaces list failures', async () => {
    const broken = {
      listSkills: () => Promise.reject(new Error('skills offline')),
      getSkill: () => Promise.reject(new Error('skills offline')),
    };
    const { result } = renderHook(() => useValkyrieSkills('env-a'), {
      wrapper: wrapWithValkyrie({ 'valkyrie.skills': broken }),
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toEqual(new Error('skills offline'));
  });
});

describe('useValkyrieSkill', () => {
  it('fetches the full record with markdown and code', async () => {
    const { result } = renderHook(
      () => useValkyrieSkill('env-k8s-valhalla', 'k8s_memory_pressure_probe'),
      { wrapper: wrapWithValkyrie() },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.content).toContain('OOMKilled');
    expect(result.current.data?.toolCode).toContain('def run(signal: dict)');
    expect(result.current.data?.testCode).toContain('def test_matches_oomkilled_pod');
  });

  it('resolves to null for an unknown skill (404)', async () => {
    const { result } = renderHook(() => useValkyrieSkill('env-k8s-valhalla', 'ghost_probe'), {
      wrapper: wrapWithValkyrie(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toBeNull();
  });

  it('stays idle until a skill name is selected', () => {
    const { result } = renderHook(() => useValkyrieSkill('env-k8s-valhalla', null), {
      wrapper: wrapWithValkyrie(),
    });
    expect(result.current.fetchStatus).toBe('idle');
  });

  it('surfaces detail failures', async () => {
    const broken = {
      listSkills: () => Promise.reject(new Error('skill offline')),
      getSkill: () => Promise.reject(new Error('skill offline')),
    };
    const { result } = renderHook(() => useValkyrieSkill('env-a', 'oom_probe'), {
      wrapper: wrapWithValkyrie({ 'valkyrie.skills': broken }),
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});
