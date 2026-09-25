/**
 * Old links into the Ravn plugin keep working.
 *
 * `/ravn/sessions?session=…&ravn_id=…&instance_id=…` was the conversation
 * page; the same conversation now lives on its ravn's Chat tab.
 */
export function legacySessionSearch(search: Record<string, unknown>): Record<string, string> {
  const text = (value: unknown) => (typeof value === 'string' && value ? value : undefined);
  const ravn = text(search.ravn_id) ?? text(search.ravn);
  const instanceId = text(search.instance_id);
  const session = text(search.session);
  return {
    ...(ravn && { ravn }),
    ...(instanceId && { instance_id: instanceId }),
    tab: 'chat',
    ...(session && { session }),
  };
}
