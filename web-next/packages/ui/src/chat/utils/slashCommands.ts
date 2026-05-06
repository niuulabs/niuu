export interface SlashCommand {
  name: string;
  type: 'command' | 'skill';
  description?: string;
}

/**
 * Build SlashCommand[] from the arrays reported by the CLI.
 */
export function buildCommandList(slashCommands: string[], skills: string[]): SlashCommand[] {
  const cmds: SlashCommand[] = slashCommands.map((name) => ({ name, type: 'command' }));
  const skillCmds: SlashCommand[] = skills.map((name) => ({ name, type: 'skill' }));
  return [...cmds, ...skillCmds];
}
