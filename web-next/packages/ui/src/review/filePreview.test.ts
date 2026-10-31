import { describe, expect, it } from 'vitest';
import { classifyPreview, isTextPreview } from './filePreview';

describe('native-compatible file preview kinds', () => {
  it.each([
    ['photo.PNG', '', 'image'],
    ['opaque', 'image/jpeg', 'image'],
    ['diagram.svg', '', 'image'],
    ['report.pdf', '', 'pdf'],
    ['opaque', 'application/pdf', 'pdf'],
    ['README.MD', '', 'markdown'],
    ['notes.markdown', '', 'markdown'],
    ['opaque', 'text/markdown', 'markdown'],
    ['flow.mmd', '', 'mermaid'],
    ['flow.mermaid', '', 'mermaid'],
    ['index.html', '', 'html'],
    ['opaque', 'text/html', 'html'],
    ['recording.mp4', '', 'video'],
    ['opaque', 'video/webm', 'video'],
    ['song.mp3', '', 'audio'],
    ['opaque', 'audio/wav', 'audio'],
    ['App.tsx', '', 'code'],
    ['Cargo.toml', '', 'code'],
    ['Dockerfile', '', 'code'],
    ['main.swift', '', 'code'],
    ['LICENSE', '', 'text'],
    ['.gitignore', '', 'text'],
    ['debug.log', '', 'text'],
    ['opaque', 'text/plain', 'text'],
    ['archive.zip', '', 'download'],
    ['unknown.bin', 'application/octet-stream', 'download'],
  ])('%s (%s) opens as %s', (name, mime, kind) =>
    expect(classifyPreview(name, mime).kind).toBe(kind),
  );
  it('maps language names and bounds only textual formats', () => {
    expect(classifyPreview('src/main.py').language).toBe('python');
    expect(classifyPreview('config.yml').language).toBe('yaml');
    expect(classifyPreview('run.sh').language).toBe('bash');
    expect(isTextPreview('markdown')).toBe(true);
    expect(isTextPreview('html')).toBe(true);
    expect(isTextPreview('video')).toBe(false);
    expect(isTextPreview('download')).toBe(false);
  });
});
