const SVG_NS = 'http://www.w3.org/2000/svg';
const FONT_SIZE = 16;
const LINE_HEIGHT = FONT_SIZE * 1.5;
/** An average glyph's width as a share of the font size, to wrap words inside a label's box. */
const GLYPH_WIDTH = 0.45;

/** The lines a label's HTML shows: a `<br>` or a new block starts a line. */
function labelLines(label: Element): string[] {
  const lines: string[] = [''];
  const walk = (node: Node) => {
    if (node.nodeType === node.TEXT_NODE) {
      lines[lines.length - 1] += node.textContent ?? '';
      return;
    }
    if (node.nodeType !== node.ELEMENT_NODE) return;
    const name = (node as Element).localName.toLowerCase();
    if (name === 'br') {
      lines.push('');
      return;
    }
    const block = name === 'p' || name === 'div';
    if (block && lines[lines.length - 1]!.trim()) lines.push('');
    node.childNodes.forEach(walk);
    if (block && lines[lines.length - 1]!.trim()) lines.push('');
  };
  label.childNodes.forEach(walk);
  return lines.map((line) => line.replace(/\s+/g, ' ').trim()).filter(Boolean);
}

/** Wrap one line's words to the label box, as the HTML label would have. */
function wrap(line: string, width: number): string[] {
  const perLine = Math.max(1, Math.floor(width / (FONT_SIZE * GLYPH_WIDTH)));
  const out: string[] = [];
  let current = '';
  for (const word of line.split(' ')) {
    const next = current ? `${current} ${word}` : word;
    if (next.length > perLine && current) {
      out.push(current);
      current = word;
    } else current = next;
  }
  if (current) out.push(current);
  return out;
}

/** The text colour a `classDef` gives a node, read from the diagram's own styles. */
function classColor(styles: string, node: Element | null): string | undefined {
  for (const name of node?.getAttribute('class')?.split(/\s+/) ?? []) {
    if (!name || name === 'node' || name === 'default') continue;
    const rule = new RegExp(`\\.${name.replace(/[^\w-]/g, '')}[^{}]*\\{([^}]*)\\}`).exec(styles);
    const color = rule && /(?:^|[;\s])color:\s*([^;!}]+)/.exec(rule[1]!);
    if (color) return color[1]!.trim();
  }
  return undefined;
}

/**
 * Mermaid writes node and edge labels as HTML inside `<foreignObject>`, which a strict SVG
 * sanitiser removes, leaving empty boxes. Rewrite each label as SVG `<text>`, centred in the
 * box it had, so the diagram keeps its words without admitting HTML into the SVG.
 */
export function foreignLabelsToText(svg: string, textColor: string): string {
  // Mermaid's labels are HTML (a bare `<br>`), so read the SVG as the page would.
  const doc = new DOMParser().parseFromString(svg, 'text/html');
  const root = doc.getElementsByTagNameNS(SVG_NS, 'svg')[0];
  if (!root) throw new Error('Mermaid did not produce an SVG');
  const styles = [...root.getElementsByTagNameNS(SVG_NS, 'style')]
    .map((style) => style.textContent ?? '')
    .join('\n');

  for (const box of [...root.getElementsByTagNameNS(SVG_NS, 'foreignObject')]) {
    const width = Number(box.getAttribute('width')) || 0;
    const height = Number(box.getAttribute('height')) || 0;
    const x = Number(box.getAttribute('x')) || 0;
    const y = Number(box.getAttribute('y')) || 0;
    const lines = labelLines(box).flatMap((line) => wrap(line, width));
    const text = doc.createElementNS(SVG_NS, 'text');
    text.setAttribute('text-anchor', 'middle');
    text.setAttribute('font-size', String(FONT_SIZE));
    text.setAttribute('fill', classColor(styles, box.closest('.node')) ?? textColor);
    const top = y + (height - lines.length * LINE_HEIGHT) / 2;
    lines.forEach((line, index) => {
      const span = doc.createElementNS(SVG_NS, 'tspan');
      span.setAttribute('x', String(x + width / 2));
      span.setAttribute('y', String(top + index * LINE_HEIGHT + LINE_HEIGHT * 0.72));
      span.textContent = line;
      text.appendChild(span);
    });
    box.replaceWith(text);
  }
  return new XMLSerializer().serializeToString(root);
}
