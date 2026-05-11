/**
 * Ravn plugin port interfaces + persona API types.
 *
 * All types in this file are either pure TypeScript interfaces (no runtime
 * cost) or type aliases. Implementations live in src/adapters/.
 */

import type { BudgetState, PersonaRole, FieldType } from '@niuulabs/domain';
import type { Ravn } from './domain/ravn';
import type { Session } from './domain/session';
import type { Trigger } from './domain/trigger';
import type { Message } from './domain/message';

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

export interface PersonaSummary {
  name: string;
  role: PersonaRole;
  letter: string;
  color: string;
  summary: string;
  permissionMode: string;
  allowedTools: string[];
  iterationBudget: number;
  isBuiltin: boolean;
  hasOverride: boolean;
  producesEvent: string;
  consumesEvents: string[];
}

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

export type PersonaFilter = 'all' | 'builtin' | 'custom';

// ---------------------------------------------------------------------------
// Port interfaces
// ---------------------------------------------------------------------------

/** CRUD store for Persona configurations. */
export interface IPersonaStore {
  listPersonas(filter?: PersonaFilter): Promise<PersonaSummary[]>;
  getPersona(name: string): Promise<PersonaDetail>;
  getPersonaYaml(name: string): Promise<string>;
  createPersona(req: PersonaCreateRequest): Promise<PersonaDetail>;
  updatePersona(name: string, req: PersonaCreateRequest): Promise<PersonaDetail>;
  deletePersona(name: string): Promise<void>;
  forkPersona(name: string, req: PersonaForkRequest): Promise<PersonaDetail>;
}

/** Read stream for Ravn fleet state. */
export interface IRavenStream {
  listRavens(): Promise<Ravn[]>;
  getRaven(id: string): Promise<Ravn>;
}

/** Read stream for Session transcripts. */
export interface ISessionStream {
  listSessions(): Promise<Session[]>;
  getSession(id: string): Promise<Session>;
  getMessages(sessionId: string): Promise<Message[]>;
}

/** CRUD store for Triggers. */
export interface ITriggerStore {
  listTriggers(): Promise<Trigger[]>;
  createTrigger(t: Omit<Trigger, 'id' | 'createdAt'>): Promise<Trigger>;
  deleteTrigger(id: string): Promise<void>;
}

/** Read stream for Budget state. */
export interface IBudgetStream {
  getBudget(ravnId: string): Promise<BudgetState>;
  getFleetBudget(): Promise<BudgetState>;
}
