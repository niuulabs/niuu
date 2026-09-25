/**
 * Persona library — families and one-line descriptions.
 *
 * Almost every persona shares the role `build`, so role says nothing about
 * where to look. Personas are named `<family>-<part>` (`developer-coder`,
 * `research-framer`), and that prefix is how people find them. A prefix shared
 * by two or more personas is a family; everything else is General.
 */

import type { PersonaSummary } from '@niuulabs/domain';

export const GENERAL_FAMILY = 'general';

export interface PersonaFamily {
  key: string;
  label: string;
  personas: PersonaSummary[];
}

export type PersonaSource = 'all' | 'in-use' | 'custom';

function prefixOf(name: string): string {
  return name.split('-')[0] ?? name;
}

export function humanizePersonaName(name: string): string {
  const spaced = name.replace(/[-_]+/g, ' ').trim();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

export function groupPersonaFamilies(personas: PersonaSummary[]): PersonaFamily[] {
  const prefixCounts = new Map<string, number>();
  for (const persona of personas) {
    const prefix = prefixOf(persona.name);
    prefixCounts.set(prefix, (prefixCounts.get(prefix) ?? 0) + 1);
  }
  const families = new Map<string, PersonaFamily>();
  for (const persona of personas) {
    const prefix = prefixOf(persona.name);
    const key = (prefixCounts.get(prefix) ?? 0) > 1 ? prefix : GENERAL_FAMILY;
    const family = families.get(key) ?? {
      key,
      label: key === GENERAL_FAMILY ? 'General' : humanizePersonaName(key),
      personas: [],
    };
    family.personas.push(persona);
    families.set(key, family);
  }
  return [...families.values()]
    .map((family) => ({
      ...family,
      personas: [...family.personas].sort((left, right) => left.name.localeCompare(right.name)),
    }))
    .sort((left, right) => {
      if (left.key === GENERAL_FAMILY) return -1;
      if (right.key === GENERAL_FAMILY) return 1;
      return left.label.localeCompare(right.label);
    });
}

/**
 * The line under a persona's name. Many built-in summaries only repeat the name
 * ("Research framer"); then what it emits says more.
 */
export function personaTagline(
  persona: Pick<PersonaSummary, 'name' | 'summary' | 'producesEvent' | 'allowedTools'>,
): string {
  const summary = persona.summary.trim();
  const echoesName = summary.toLowerCase() === humanizePersonaName(persona.name).toLowerCase();
  if (summary && !echoesName) return summary;
  if (persona.producesEvent) return `emits ${persona.producesEvent}`;
  return `${persona.allowedTools.length} tools`;
}

export function isCustomPersona(
  persona: Pick<PersonaSummary, 'isBuiltin' | 'hasOverride'>,
): boolean {
  return !persona.isBuiltin || persona.hasOverride;
}

export function matchesPersonaQuery(persona: PersonaSummary, query: string): boolean {
  const needle = query.trim().toLowerCase();
  if (!needle) return true;
  return [
    persona.name,
    persona.summary,
    persona.producesEvent,
    ...persona.consumesEvents,
    ...persona.allowedTools,
  ]
    .join(' ')
    .toLowerCase()
    .includes(needle);
}

export function matchesPersonaSource(
  persona: PersonaSummary,
  source: PersonaSource,
  usage: (name: string) => number,
): boolean {
  switch (source) {
    case 'all':
      return true;
    case 'in-use':
      return usage(persona.name) > 0;
    case 'custom':
      return isCustomPersona(persona);
  }
}
