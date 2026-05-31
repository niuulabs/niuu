import { describe, expect, it, vi } from 'vitest';
import { screen } from '@testing-library/react';
import { usePluginCtx, useService } from '@niuulabs/plugin-sdk';
import { createMimirMockAdapter } from '../adapters/mock';
import { createStatefulWardenService, renderWithMimir, slugify } from './renderWithMimir';

describe('renderWithMimir helpers', () => {
  it('slugifies names safely', () => {
    expect(slugify('  Raven Keeper  ')).toBe('raven-keeper');
    expect(slugify('UPPER_case/name')).toBe('upper-case-name');
    expect(slugify('***')).toBe('');
  });

  it('provides plugin context and services to rendered children', () => {
    function Probe() {
      const pluginCtx = usePluginCtx();
      const mimir = useService('mimir');
      const wardens = useService('ravn.wardens');
      return (
        <div>
          <span data-testid="tweak">{pluginCtx.tweaks['mimir.selectedWardenId']}</span>
          <span data-testid="has-mimir">{String(Boolean(mimir))}</span>
          <span data-testid="has-wardens">{String(Boolean(wardens))}</span>
        </div>
      );
    }

    renderWithMimir(<Probe />, undefined, { tweaks: { 'mimir.selectedWardenId': 'ravn-fjolnir' } });
    expect(screen.getByTestId('tweak')).toHaveTextContent('ravn-fjolnir');
    expect(screen.getByTestId('has-mimir')).toHaveTextContent('true');
    expect(screen.getByTestId('has-wardens')).toHaveTextContent('true');
  });

  it('creates, subscribes, observes, installs, starts, stops, and uninstalls wardens', async () => {
    const service = createStatefulWardenService(createMimirMockAdapter());
    const existing = await service.listWardens();
    expect(existing.length).toBeGreaterThan(0);

    const created = await service.createWarden({
      name: '  Fresh Warden  ',
      persona: 'custom-persona',
      profile: 'profile',
      deployment: 'k8s-apply',
      mountNames: ['local'],
      readMountNames: ['local'],
      writeMountNames: ['local'],
      writeMount: 'local',
      autostart: true,
      console: { enabled: false, host: '127.0.0.1', port: 9300, publicHost: '', authMode: 'noop' },
      schedules: { dreamCycleCronExpression: '*/5 * * * *' },
      createdBy: 'tester',
    });
    expect(created.id).toBe('fresh-warden');
    expect(created.supervisor?.installed).toBe(false);

    const listener = vi.fn();
    const unsubscribe = service.subscribeWarden(created.id, listener);

    const observedMissing = await service.observeWarden(created.id);
    expect(observedMissing.supervisor?.observation?.status).toBe('missing');

    const installed = await service.installWarden(created.id);
    expect(installed.supervisor?.installed).toBe(true);

    const observedIdle = await service.observeWarden(created.id);
    expect(observedIdle.supervisor?.observation?.status).toBe('idle');

    const started = await service.startWarden(created.id);
    expect(started.runtime?.state).toBe('active');
    expect((await service.observeWarden(created.id)).supervisor?.observation?.status).toBe(
      'running',
    );

    const stopped = await service.stopWarden(created.id);
    expect(stopped.runtime?.state).toBe('idle');

    const removed = await service.uninstallWarden(created.id);
    expect(removed.runtime?.state).toBe('offline');
    expect(removed.supervisor?.installed).toBe(false);

    expect(listener).toHaveBeenCalled();
    unsubscribe();
  });

  it('rejects invalid lifecycle transitions and supports log and activity helpers', async () => {
    const service = createStatefulWardenService(createMimirMockAdapter());
    const created = await service.createWarden({ name: 'Needs Install' });

    await expect(service.startWarden(created.id)).rejects.toThrow(
      /installed before it can be started/i,
    );
    await expect(service.stopWarden(created.id)).rejects.toThrow(
      /installed before it can be stopped/i,
    );
    await expect(service.getWarden('missing')).rejects.toThrow(/Warden not found/i);
    await expect(service.observeWarden('missing')).rejects.toThrow(/Warden not found/i);
    await expect(service.uninstallWarden('missing')).rejects.toThrow(/Warden not found/i);

    const stdout = await service.getWardenLogs(created.id, { stream: 'stdout', limit: 1 });
    const stderr = await service.getWardenLogs(created.id, { stream: 'stderr', limit: 5 });
    const activity = await service.getWardenActivity(created.id, 3);

    expect(stdout).toHaveLength(1);
    expect(stderr[0]?.source).toBe('stderr');
    expect(activity).toHaveLength(3);
    expect(activity[activity.length - 1]?.message).toContain('idle');
  });

  it('reuses unique ids when similarly named wardens are created', async () => {
    const service = createStatefulWardenService(createMimirMockAdapter());
    const first = await service.createWarden({ name: 'Duplicate' });
    const second = await service.createWarden({ name: 'Duplicate' });
    expect(first.id).toBe('duplicate');
    expect(second.id).toBe('duplicate-2');
  });
});
