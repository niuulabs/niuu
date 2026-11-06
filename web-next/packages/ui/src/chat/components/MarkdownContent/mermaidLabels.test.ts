import { describe, expect, it } from 'vitest';
import { foreignLabelsToText } from './mermaidLabels';

// The shapes Mermaid 12 writes, trimmed: HTML labels inside foreignObject.
const svg = `<svg xmlns="http://www.w3.org/2000/svg" id="m">
<style>#m .source&gt;*{fill:#E8F1FF!important;color:#102A43!important;}</style>
<g class="node default source" id="m-A"><rect/><g class="label" transform="translate(-60, -31)">
<foreignObject width="120" height="63"><div xmlns="http://www.w3.org/1999/xhtml"><span class="nodeLabel"><p>Agent writes<br>workspace file</p></span></div></foreignObject></g></g>
<g class="edgeLabel"><g class="label"><foreignObject x="4" width="140" height="24"><div xmlns="http://www.w3.org/1999/xhtml"><span class="edgeLabel"><p>Browse or download</p></span></div></foreignObject></g></g>
<g class="node default" id="m-B"><g class="label"><foreignObject width="80" height="24"><div xmlns="http://www.w3.org/1999/xhtml"><span class="nodeLabel"><p>Operator need now</p></span></div></foreignObject></g></g>
</svg>`;

function parse(out: string) {
  return new DOMParser().parseFromString(out, 'image/svg+xml');
}

describe('foreignLabelsToText', () => {
  it('rewrites every HTML label as centred SVG text in the box it had', () => {
    const doc = parse(foreignLabelsToText(svg, '#eeeeee'));
    expect(doc.getElementsByTagName('foreignObject')).toHaveLength(0);
    const texts = [...doc.getElementsByTagName('text')];
    expect(texts.map((t) => [...t.children].map((s) => s.textContent))).toEqual([
      ['Agent writes', 'workspace file'],
      ['Browse or download'],
      // A label wider than its box wraps, as the HTML label did.
      ['Operator', 'need now'],
    ]);
    const [node, edge] = texts;
    expect(node!.getAttribute('text-anchor')).toBe('middle');
    expect(node!.children[0]!.getAttribute('x')).toBe('60');
    expect(edge!.children[0]!.getAttribute('x')).toBe('74');
  });

  it("takes a classDef's text colour, and the theme's otherwise", () => {
    const texts = [...parse(foreignLabelsToText(svg, '#eeeeee')).getElementsByTagName('text')];
    expect(texts.map((t) => t.getAttribute('fill'))).toEqual(['#102A43', '#eeeeee', '#eeeeee']);
  });

  it('returns well-formed SVG an image can load', () => {
    const out = foreignLabelsToText(svg, '#eeeeee');
    expect(out.startsWith('<svg xmlns="http://www.w3.org/2000/svg"')).toBe(true);
    expect(
      new DOMParser().parseFromString(out, 'image/svg+xml').getElementsByTagName('parsererror'),
    ).toHaveLength(0);
  });

  it('refuses output that holds no SVG rather than drawing an empty diagram', () => {
    expect(() => foreignLabelsToText('<p>Syntax error</p>', '#fff')).toThrow(/did not produce/);
  });
});
