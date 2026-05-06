import { personaRoleSchema } from '@niuulabs/domain';
import type { EventCatalog } from '@niuulabs/domain';
import type { PersonaCreateRequest } from '../ports';

export interface PersonaValidationError {
  field: string;
  message: string;
}

/**
 * Validate a PersonaCreateRequest against the live EventCatalog.
 *
 * Rules:
 * 1. `name` must be non-empty.
 * 2. `role` must be one of the nine canonical PersonaRole values.
 * 3. `allowed` and `forbidden` tool lists must be disjoint.
 * 4. `produces.eventType`, if set, must exist in the EventCatalog.
 * 5. Each `consumes.events[].name`, if set, must exist in the EventCatalog.
 */
export function validatePersona(
  req: PersonaCreateRequest,
  catalog: EventCatalog,
): PersonaValidationError[] {
  const errors: PersonaValidationError[] = [];

  if (!req.name.trim()) {
    errors.push({ field: 'name', message: 'Name is required' });
  }

  const roleResult = personaRoleSchema.safeParse(req.role);
  if (!roleResult.success) {
    errors.push({
      field: 'role',
      message: `"${String(req.role)}" is not a valid role`,
    });
  }

  const overlap = req.allowedTools.filter((t) => req.forbiddenTools.includes(t));
  if (overlap.length > 0) {
    errors.push({
      field: 'tools',
      message: `Allow and deny lists must be disjoint — overlap: ${overlap.join(', ')}`,
    });
  }

  const catalogNames = new Set(catalog.map((e) => e.name));

  if (req.producesEventType && !catalogNames.has(req.producesEventType)) {
    errors.push({
      field: 'produces.eventType',
      message: `Event "${req.producesEventType}" is not in the EventCatalog`,
    });
  }

  for (const consumed of req.consumesEvents) {
    if (consumed.name && !catalogNames.has(consumed.name)) {
      errors.push({
        field: `consumes.${consumed.name}`,
        message: `Consumed event "${consumed.name}" is not in the EventCatalog`,
      });
    }
  }

  return errors;
}
