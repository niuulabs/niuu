import { describe, expect, it } from 'vitest';
import {
  LIVE_PREVIEW_DISABLED,
  findLocalUrls,
  isPreviewPortsFrame,
  portsFromFrame,
  previewOrigin,
  previewUrl,
} from './livePreview';

const config = { enabled: true, originTemplate: 'https://{port}--{session_id}.preview.niuu.world' };

describe('previewOrigin', () => {
  it('fills the port and session into the template', () => {
    expect(previewOrigin(config, 's-1', 5173)).toBe('https://5173--s-1.preview.niuu.world');
  });

  it('is null when previews are off or the port is not a TCP port', () => {
    expect(previewOrigin(LIVE_PREVIEW_DISABLED, 's-1', 5173)).toBeNull();
    expect(previewOrigin(config, 's-1', 0)).toBeNull();
    expect(previewOrigin(config, 's-1', 70000)).toBeNull();
  });
});

describe('previewUrl', () => {
  it('appends the path, adding the leading slash a typed path may lack', () => {
    expect(previewUrl(config, 's', 3000, '/docs?x=1')).toBe(
      'https://3000--s.preview.niuu.world/docs?x=1',
    );
    expect(previewUrl(config, 's', 3000, ' docs ')).toBe('https://3000--s.preview.niuu.world/docs');
    expect(previewUrl(config, 's', 3000)).toBe('https://3000--s.preview.niuu.world/');
    expect(previewUrl(LIVE_PREVIEW_DISABLED, 's', 3000)).toBeNull();
  });

  it('refuses a typed path that would leave the preview origin', () => {
    expect(previewUrl(config, 's', 3000, '//evil.example/x')).toBeNull();
    expect(previewUrl(config, 's', 3000, '/\\evil.example/x')).toBeNull();
    expect(previewUrl(config, 's', 3000, 'javascript:alert(1)')).toBe(
      'https://3000--s.preview.niuu.world/javascript:alert(1)',
    );
    expect(previewUrl(config, 's', 3000, '@evil.example/')).toBe(
      'https://3000--s.preview.niuu.world/@evil.example/',
    );
  });
});

describe('preview_ports frames', () => {
  it('recognises the frame and keeps only real ports, sorted and unique', () => {
    const frame = {
      type: 'preview_ports',
      ports: [8080, 5173, 5173, 0, 1.5],
      opened: [],
      closed: [],
    };
    expect(isPreviewPortsFrame(frame)).toBe(true);
    expect(portsFromFrame(frame as never)).toEqual([5173, 8080]);
    expect(isPreviewPortsFrame({ type: 'assistant' })).toBe(false);
    expect(isPreviewPortsFrame(null)).toBe(false);
    expect(isPreviewPortsFrame({ type: 'preview_ports' })).toBe(false);
  });
});

describe('findLocalUrls', () => {
  it('finds loopback URLs with their port and path', () => {
    const text =
      'Vite runs at http://localhost:5173/docs. The API is on 127.0.0.1:8000, and 0.0.0.0:3000 too.';
    expect(findLocalUrls(text).map(({ text, port, path }) => ({ text, port, path }))).toEqual([
      { text: 'http://localhost:5173/docs', port: 5173, path: '/docs' },
      { text: '127.0.0.1:8000', port: 8000, path: '/' },
      { text: '0.0.0.0:3000', port: 3000, path: '/' },
    ]);
  });

  it('reads IPv6 loopback and ignores bare hosts, other hosts and impossible ports', () => {
    expect(findLocalUrls('http://[::1]:4321/')[0]?.port).toBe(4321);
    expect(findLocalUrls('see localhost and example.com:80 and localhost:99999')).toEqual([]);
  });
});
