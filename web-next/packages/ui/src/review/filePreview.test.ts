import { afterEach, describe, expect, it, vi } from 'vitest';
import { classifyPreview, imageMimeFor, isTextPreview } from './filePreview';
import { loadPreview } from './ResourcePreviewContent';

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

describe('imageMimeFor', () => {
  it('names the image type a file name implies', () => {
    expect(imageMimeFor('docs/layout.SVG')).toBe('image/svg+xml');
    expect(imageMimeFor('photo.jpg')).toBe('image/jpeg');
    expect(imageMimeFor('notes.md')).toBeUndefined();
  });
});

describe('loadPreview', () => {
  const original = URL.createObjectURL;
  afterEach(() => {
    URL.createObjectURL = original;
  });

  it('types an untyped image download so the browser can draw it', async () => {
    const createObjectURL = vi.fn((_blob: Blob) => 'blob:preview');
    URL.createObjectURL = createObjectURL;
    const untyped = new Blob(['<svg xmlns="http://www.w3.org/2000/svg"/>'], {
      type: 'application/octet-stream',
    });
    const preview = await loadPreview(untyped, 'layout.svg');
    expect(preview.kind).toBe('image');
    expect(createObjectURL.mock.calls[0]![0].type).toBe('image/svg+xml');

    const typed = new Blob(['png'], { type: 'image/png' });
    await loadPreview(typed, 'shot.png');
    expect(createObjectURL.mock.calls[1]![0]).toBe(typed);
  });
});
