/**
 * Ravn plugin port interfaces + persona API types.
 *
 * All types in this file are either pure TypeScript interfaces (no runtime
 * cost) or type aliases. Implementations live in src/adapters/.
 */

import type { FieldType, IPersonaCatalog, PersonaRole, PersonaSummary } from '@niuulabs/domain';
import type { CreatedTrigger, Trigger } from './domain/trigger';

// ---------------------------------------------------------------------------
// Persona API types — migrated from web/src/modules/ravn/api/types.ts
// (camelCase client representation of the snake_case server responses)
// ---------------------------------------------------------------------------

export interface PersonaLLM {
  thinkingEnabled: boolean;
  maxTokens: number;
  temperature?: number;
}

export interface PersonaProduces {
  eventType: string;
  schemaDef: Record<string, FieldType>;
}

export interface PersonaConsumesEvent {
  name: string;
  injects?: string[];
  trust?: number;
}

export interface PersonaConsumes {
  events: PersonaConsumesEvent[];
  schemaDef: Record<string, FieldType>;
}

export interface PersonaFanIn {
  strategy: string;
  params: Record<string, unknown>;
}

export type { PersonaFilter, PersonaSummary } from '@niuulabs/domain';

export interface PersonaDetail extends PersonaSummary {
  description: string;
  systemPromptTemplate: string;
  forbiddenTools: string[];
  llm: PersonaLLM;
  produces: PersonaProduces;
  consumes: PersonaConsumes;
  fanIn?: PersonaFanIn;
  mimirWriteRouting?: 'local' | 'shared' | 'domain';
  yamlSource: string;
  overrideSource?: string;
}

export interface PersonaCreateRequest {
  name: string;
  role: PersonaRole;
  letter: string;
  color: string;
  summary: string;
  description: string;
  systemPromptTemplate: string;
  allowedTools: string[];
  forbiddenTools: string[];
  permissionMode: string;
  iterationBudget: number;
  llmThinkingEnabled: boolean;
  llmMaxTokens: number;
  llmTemperature?: number;
  producesEventType: string;
  producesSchema: Record<string, FieldType>;
  consumesEvents: PersonaConsumesEvent[];
  consumesSchema: Record<string, FieldType>;
  fanInStrategy?: string;
  fanInParams?: Record<string, unknown>;
  mimirWriteRouting?: 'local' | 'shared' | 'domain';
}

export interface PersonaForkRequest {
  newName: string;
}

// ---------------------------------------------------------------------------
// Port interfaces
// ---------------------------------------------------------------------------

/** CRUD store for Persona configurations. */
export interface IPersonaStore extends IPersonaCatalog {
  getPersona(name: string): Promise<PersonaDetail>;
  getPersonaYaml(name: string): Promise<string>;
  createPersona(req: PersonaCreateRequest): Promise<PersonaDetail>;
  updatePersona(name: string, req: PersonaCreateRequest): Promise<PersonaDetail>;
  deletePersona(name: string): Promise<void>;
  forkPersona(name: string, req: PersonaForkRequest): Promise<PersonaDetail>;
}

/** CRUD store for Triggers. */
export interface ITriggerStore {
  listTriggers(): Promise<Trigger[]>;
  createTrigger(t: Omit<Trigger, 'id' | 'createdAt'>): Promise<CreatedTrigger>;
  deleteTrigger(id: string): Promise<void>;
}
