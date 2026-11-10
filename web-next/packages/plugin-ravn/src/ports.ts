/**
 * Ravn plugin port interfaces + persona API types.
 *
 * All types in this file are either pure TypeScript interfaces (no runtime
 * cost) or type aliases. Implementations live in src/adapters/.
 */

import type { FieldType, IPersonaCatalog, PersonaSummary } from '@niuulabs/domain';

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
  /** For each outcome, the persona's rule for when to report it. */
  when: Record<string, string>;
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
  llm: PersonaLLM;
  produces: PersonaProduces;
  consumes: PersonaConsumes;
  fanIn?: PersonaFanIn;
  mimirWriteRouting?: 'local' | 'shared' | 'domain';
  yamlSource: string;
  overrideSource?: string;
}

/**
 * A persona as its document: the fields its YAML declares (`name`, `description`,
 * `produces`, …), exactly what is saved and what a workflow pins.
 */
export type PersonaDocument = Record<string, unknown>;

/** Whether a persona's YAML can be saved, and if not, why. */
export interface PersonaYamlCheck {
  valid: boolean;
  errors: string[];
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
  /** The persona's whole document, as written (a built-in's source, not its runtime form). */
  getPersonaDocument(name: string): Promise<PersonaDocument>;
  /** Check YAML as saving it would, without saving it. */
  checkPersonaYaml(yaml: string): Promise<PersonaYamlCheck>;
  /** Create a persona from its YAML; its name is the persona's name. */
  createPersonaFromYaml(yaml: string): Promise<PersonaDetail>;
  /** Replace a persona (or override a built-in) with its YAML. */
  replacePersonaYaml(name: string, yaml: string): Promise<PersonaDetail>;
  deletePersona(name: string): Promise<void>;
  forkPersona(name: string, req: PersonaForkRequest): Promise<PersonaDetail>;
}
