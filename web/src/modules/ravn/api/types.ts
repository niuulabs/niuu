/**
 * Ravn Persona domain types.
 *
 * Camel-case client representation of the snake_case server responses.
 */

// ---------------------------------------------------------------------------
// Persona sub-types
// ---------------------------------------------------------------------------

export interface PersonaLLM {
  primaryAlias: string;
  thinkingEnabled: boolean;
  maxTokens: number;
}

export interface PersonaProduces {
  eventType: string;
  schemaDef: Record<string, unknown>;
}

export interface PersonaConsumes {
  eventTypes: string[];
  injects: string[];
}

export interface PersonaFanIn {
  strategy: string;
  contributesTo: string;
}

// ---------------------------------------------------------------------------
// Persona summary (list view)
// ---------------------------------------------------------------------------

export interface PersonaSummary {
  name: string;
  permissionMode: string;
  allowedTools: string[];
  iterationBudget: number;
  isBuiltin: boolean;
  hasOverride: boolean;
  producesEvent: string;
  consumesEvents: string[];
}

// ---------------------------------------------------------------------------
// Persona detail (full view)
// ---------------------------------------------------------------------------

export interface PersonaDetail extends PersonaSummary {
  systemPromptTemplate: string;
  forbiddenTools: string[];
  llm: PersonaLLM;
  produces: PersonaProduces;
  consumes: PersonaConsumes;
  fanIn: PersonaFanIn;
  yamlSource: string;
}

// ---------------------------------------------------------------------------
// Create / update request
// ---------------------------------------------------------------------------

export interface PersonaCreateRequest {
  name: string;
  systemPromptTemplate: string;
  allowedTools: string[];
  forbiddenTools: string[];
  permissionMode: string;
  iterationBudget: number;
  llmPrimaryAlias: string;
  llmThinkingEnabled: boolean;
  llmMaxTokens: number;
  producesEventType: string;
  consumesEventTypes: string[];
  consumesInjects: string[];
  fanInStrategy: string;
  fanInContributesTo: string;
}

// ---------------------------------------------------------------------------
// Fork request
// ---------------------------------------------------------------------------

export interface PersonaForkRequest {
  newName: string;
}

// ---------------------------------------------------------------------------
// Filter
// ---------------------------------------------------------------------------

export type PersonaFilter = 'all' | 'builtin' | 'custom';
