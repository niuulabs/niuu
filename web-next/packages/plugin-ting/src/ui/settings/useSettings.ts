import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type {
  ITingSettingsService,
  FlockConfig,
  DispatchDefaults,
  NotificationSettings,
} from '../../ports';

export function useFlockConfig() {
  const settings = useService<ITingSettingsService>('ting.settings');
  return useQuery({
    queryKey: ['ting', 'settings', 'flock'],
    queryFn: () => settings.getFlockConfig(),
  });
}

export function useUpdateFlockConfig() {
  const settings = useService<ITingSettingsService>('ting.settings');
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (patch: Partial<Omit<FlockConfig, 'updatedAt'>>) =>
      settings.updateFlockConfig(patch),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['ting', 'settings', 'flock'] });
    },
  });
}

export function useDispatchDefaults() {
  const settings = useService<ITingSettingsService>('ting.settings');
  return useQuery({
    queryKey: ['ting', 'settings', 'dispatch'],
    queryFn: () => settings.getDispatchDefaults(),
  });
}

export function useUpdateDispatchDefaults() {
  const settings = useService<ITingSettingsService>('ting.settings');
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (patch: Partial<Omit<DispatchDefaults, 'updatedAt'>>) =>
      settings.updateDispatchDefaults(patch),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['ting', 'settings', 'dispatch'] });
    },
  });
}

export function useNotificationSettings() {
  const settings = useService<ITingSettingsService>('ting.settings');
  return useQuery({
    queryKey: ['ting', 'settings', 'notifications'],
    queryFn: () => settings.getNotificationSettings(),
  });
}

export function useUpdateNotificationSettings() {
  const settings = useService<ITingSettingsService>('ting.settings');
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (patch: Partial<Omit<NotificationSettings, 'updatedAt'>>) =>
      settings.updateNotificationSettings(patch),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['ting', 'settings', 'notifications'] });
    },
  });
}
