import type { Template } from '../domain/template';
import type { PodSpec } from '../domain/pod';

/** Port for storing and retrieving reusable pod templates. */
export interface ITemplateStore {
  getTemplate(id: string): Promise<Template | null>;
  listTemplates(): Promise<Template[]>;
  createTemplate(name: string, spec: PodSpec): Promise<Template>;
  updateTemplate(id: string, spec: PodSpec): Promise<Template>;
  deleteTemplate(id: string): Promise<void>;
}
