import { useMemo } from 'react';
import { usePersonaYaml } from './usePersona';

// ── YAML tokenizer ─────────────────────────────────────────────────────────

type TokenType = 'key' | 'string' | 'number' | 'boolean' | 'comment' | 'punctuation' | 'plain';

interface YamlToken {
  type: TokenType;
  text: string;
}

function tokenizeLine(line: string): YamlToken[] {
  if (/^\s*#/.test(line)) {
    return [{ type: 'comment', text: line }];
  }

  const keyMatch = line.match(/^(\s*)([A-Za-z_][\w.-]*)(\s*:)(.*)$/);
  if (keyMatch) {
    const [, indent, key, colon, rest] = keyMatch;
    const tokens: YamlToken[] = [];

    if (indent) tokens.push({ type: 'plain', text: indent });
    tokens.push({ type: 'key', text: key! });
    tokens.push({ type: 'punctuation', text: colon! });

    if (rest!.trim()) {
      const trimmed = rest!.trim();
      if (/^\d+(\.\d+)?$/.test(trimmed)) {
        tokens.push({ type: 'number', text: rest! });
      } else if (/^(true|false|null)$/.test(trimmed)) {
        tokens.push({ type: 'boolean', text: rest! });
      } else {
        tokens.push({ type: 'string', text: rest! });
      }
    }

    return tokens;
  }

  const listMatch = line.match(/^(\s*-\s+)(.*)$/);
  if (listMatch) {
    return [
      { type: 'plain', text: listMatch[1]! },
      { type: 'string', text: listMatch[2]! },
    ];
  }

  return [{ type: 'plain', text: line }];
}

const TOKEN_CLASSES: Record<TokenType, string> = {
  key: 'niuu-text-status-cyan',
  string: 'niuu-text-status-emerald',
  number: 'niuu-text-status-amber',
  boolean: 'niuu-text-status-purple',
  comment: 'niuu-text-text-muted niuu-italic',
  punctuation: 'niuu-text-text-muted',
  plain: 'niuu-text-text-secondary',
};

// ── Component ──────────────────────────────────────────────────────────────

export interface PersonaYamlProps {
  name: string;
}

export function PersonaYaml({ name }: PersonaYamlProps) {
  const { data, isLoading, isError, error } = usePersonaYaml(name);

  const tokenizedLines = useMemo(() => {
    if (!data) return [];
    return data.split('\n').map((line, i) => ({
      lineNumber: i + 1,
      tokens: tokenizeLine(line),
    }));
  }, [data]);

  if (isLoading) {
    return (
      <div
        data-testid="persona-yaml-loading"
        className="niuu-p-6 niuu-text-sm niuu-text-text-muted"
      >
        Loading YAML…
      </div>
    );
  }

  if (isError) {
    return (
      <div data-testid="persona-yaml-error" className="niuu-p-6 niuu-text-sm niuu-text-critical">
        {error instanceof Error ? error.message : 'Failed to load YAML'}
      </div>
    );
  }

  return (
    <div className="niuu-overflow-auto niuu-h-full niuu-p-6" data-testid="persona-yaml">
      <pre className="niuu-m-0 niuu-font-mono niuu-text-xs niuu-leading-relaxed">
        {tokenizedLines.map(({ lineNumber, tokens }) => (
          <div key={lineNumber} className="niuu-flex">
            <span
              className="niuu-select-none niuu-text-right niuu-shrink-0 niuu-mr-4 niuu-w-8 niuu-text-text-muted niuu-opacity-50"
              data-testid="yaml-line-number"
              aria-hidden="true"
            >
              {lineNumber}
            </span>
            <span className="niuu-flex-1">
              {tokens.map((token, j) => (
                <span key={j} className={TOKEN_CLASSES[token.type]}>
                  {token.text}
                </span>
              ))}
            </span>
          </div>
        ))}
      </pre>
    </div>
  );
}
