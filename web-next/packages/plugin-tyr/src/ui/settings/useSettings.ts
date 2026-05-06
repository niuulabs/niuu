import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type {
  ITyrSettingsService,
  FlockConfig,
  DispatchDefaults,
  NotificationSettings,
} from '../../ports';

export function useFlockConfig() {
  const settings = useService<ITyrSettingsService>('tyr.settings');
  return useQuery({
    queryKey: ['tyr', 'settings', 'flock'],
    queryFn: () => settings.getFlockConfig(),
  });
}

export function useUpdateFlockConfig() {
  const settings = useService<ITyrSettingsService>('tyr.settings');
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (patch: Partial<Omit<FlockConfig, 'updatedAt'>>) =>
      settings.updateFlockConfig(patch),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['tyr', 'settings', 'flock'] });
    },
  });
}

export function useDispatchDefaults() {
  const settings = useService<ITyrSettingsService>('tyr.settings');
  return useQuery({
    queryKey: ['tyr', 'settings', 'dispatch'],
    queryFn: () => settings.getDispatchDefaults(),
  });
}

export function useUpdateDispatchDefaults() {
  const settings = useService<ITyrSettingsService>('tyr.settings');
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (patch: Partial<Omit<DispatchDefaults, 'updatedAt'>>) =>
      settings.updateDispatchDefaults(patch),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['tyr', 'settings', 'dispatch'] });
    },
  });
}

export function useNotificationSettings() {
  const settings = useService<ITyrSettingsService>('tyr.settings');
  return useQuery({
    queryKey: ['tyr', 'settings', 'notifications'],
    queryFn: () => settings.getNotificationSettings(),
  });
}

export function useUpdateNotificationSettings() {
  const settings = useService<ITyrSettingsService>('tyr.settings');
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (patch: Partial<Omit<NotificationSettings, 'updatedAt'>>) =>
      settings.updateNotificationSettings(patch),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['tyr', 'settings', 'notifications'] });
    },
  });
}
