import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { ITemplateStore } from '../ports/ITemplateStore';
import type { PodSpec } from '../domain/pod';

/** Queries all pod templates from the template store. */
export function useTemplates() {
  const store = useService<ITemplateStore>('volundr.templates');
  return useQuery({
    queryKey: ['volundr', 'templates'],
    queryFn: () => store.listTemplates(),
  });
}

/** Mutation: update an existing template's spec (increments version). */
export function useUpdateTemplate() {
  const store = useService<ITemplateStore>('volundr.templates');
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, spec }: { id: string; spec: PodSpec }) => store.updateTemplate(id, spec),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['volundr', 'templates'] });
    },
  });
}

/** Mutation: create a new template from scratch. */
export function useCreateTemplate() {
  const store = useService<ITemplateStore>('volundr.templates');
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ name, spec }: { name: string; spec: PodSpec }) =>
      store.createTemplate(name, spec),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['volundr', 'templates'] });
    },
  });
}

/** Mutation: delete a template by ID. */
export function useDeleteTemplate() {
  const store = useService<ITemplateStore>('volundr.templates');
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => store.deleteTemplate(id),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['volundr', 'templates'] });
    },
  });
}
