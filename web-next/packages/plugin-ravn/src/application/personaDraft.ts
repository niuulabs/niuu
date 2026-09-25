import type { PersonaCreateRequest } from '../ports';
import { humanizePersonaName } from './personaFamilies';

/** Persona names are file names on the server: lowercase words joined by dashes. */
export const PERSONA_NAME_PATTERN = /^[a-z0-9][a-z0-9-]*$/;

export function personaNameProblem(name: string, taken: readonly string[]): string | null {
  const trimmed = name.trim();
  if (!trimmed) return 'Give it a name.';
  if (!PERSONA_NAME_PATTERN.test(trimmed)) {
    return 'Use lowercase letters, digits and dashes, starting with a letter or digit.';
  }
  if (taken.includes(trimmed)) return `A persona named ${trimmed} already exists.`;
  return null;
}

/** The smallest valid persona: a name, a sentence, and nothing it may do yet. */
export function buildDraftPersona(name: string): PersonaCreateRequest {
  const label = humanizePersonaName(name) || 'New persona';
  return {
    name,
    role: 'build',
    letter: '',
    color: '',
    summary: label,
    description: `${label} persona.`,
    systemPromptTemplate: `# ${name}\nYou are the ${name} persona.`,
    allowedTools: [],
    forbiddenTools: [],
    permissionMode: 'default',
    iterationBudget: 20,
    llmThinkingEnabled: false,
    llmMaxTokens: 8192,
    producesEventType: '',
    producesSchema: {},
    consumesEvents: [],
    consumesSchema: {},
  };
}
