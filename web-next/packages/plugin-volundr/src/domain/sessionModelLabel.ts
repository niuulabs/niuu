/** Human-readable provider IDs without changing the selected model or guessing its version. */
export function sessionModelLabel(model: string): string {
  const claude = /^claude-(opus|sonnet|haiku|fable)-(\d+)(?:[.-](\d{1,2}))?(?:-\d{8})?$/i.exec(
    model,
  );
  if (claude) {
    const family = claude[1]!;
    return `Claude ${family[0]!.toUpperCase()}${family.slice(1)} ${claude[2]}${claude[3] ? `.${claude[3]}` : ''}`;
  }
  const gpt = /^gpt-(\d+(?:\.\d+)?)(?:-(astra|sol|terra|luna))?$/i.exec(model);
  if (gpt) return `GPT-${gpt[1]}${gpt[2] ? ` ${gpt[2][0]!.toUpperCase()}${gpt[2].slice(1)}` : ''}`;
  return model;
}
