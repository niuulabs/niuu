import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { ImagePreview, zoomView } from './ImagePreview';
import { loadImageBlob, imageAsPng } from './imageActions';

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('image preview', () => {
  it('zooms, pans by keyboard, fits, reports loading failures, and keeps an escape to the original', () => {
    render(<ImagePreview src="https://example.test/diagram.png" name="Diagram" />);
    const canvas = screen.getByRole('region');
    const image = screen.getByRole('img', { name: 'Diagram' });
    const original = image.getAttribute('viewBox');
    fireEvent.click(screen.getByRole('button', { name: 'Zoom in' }));
    expect(screen.getByText('125%')).toBeInTheDocument();
    fireEvent.keyDown(canvas, { key: 'ArrowRight' });
    expect(image.getAttribute('viewBox')).not.toBe(original);
    fireEvent.keyDown(canvas, { key: '0' });
    expect(image).toHaveAttribute('viewBox', original);
    fireEvent.wheel(canvas, { deltaY: -10, clientX: 200, clientY: 100 });
    expect(screen.getByText('125%')).toBeInTheDocument();
    fireEvent.doubleClick(canvas);
    expect(screen.getByText('100%')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Zoom out' }));
    expect(screen.getByText('80%')).toBeInTheDocument();
    fireEvent.keyDown(canvas, { key: '+' });
    fireEvent.keyDown(canvas, { key: '-' });
    fireEvent.keyDown(canvas, { key: 'Home' });
    fireEvent.error(image.querySelector('image')!);
    expect(screen.getByRole('alert')).toHaveTextContent('could not be loaded');
    expect(screen.getByRole('button', { name: 'Copy image' })).toBeDisabled();
    expect(screen.getByRole('link', { name: 'Open original image' })).toHaveAttribute(
      'target',
      '_blank',
    );
    expect(zoomView({ zoom: 8, x: 0, y: 0 }, 2, 20, 20).zoom).toBe(8);
    expect(zoomView({ zoom: 0.25, x: 0, y: 0 }, 0.5, 20, 20).zoom).toBe(0.25);
  });

  it('copies image bytes and downloads the local blob without another request', async () => {
    const blob = new Blob(['PNG'], { type: 'image/png' });
    const write = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal('navigator', { clipboard: { write } });
    const items: Record<string, Promise<Blob>>[] = [];
    vi.stubGlobal(
      'ClipboardItem',
      class {
        constructor(data: Record<string, Promise<Blob>>) {
          items.push(data);
        }
      },
    );
    vi.stubGlobal('fetch', vi.fn());
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:download');
    render(<ImagePreview src="blob:preview" blob={blob} name="diagram.png" />);
    fireEvent.click(screen.getByRole('button', { name: 'Copy image' }));
    expect(await screen.findByText('Image copied')).toBeInTheDocument();
    expect(await items[0]!['image/png']).toBe(blob);
    expect(write).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button', { name: 'Download' }));
    expect(await screen.findByText('Download started')).toBeInTheDocument();
    expect(click).toHaveBeenCalledTimes(1);
    expect(fetch).not.toHaveBeenCalled();
  });

  it('reports remote CORS and clipboard failures without navigating or claiming success', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
    vi.stubGlobal('navigator', {});
    render(<ImagePreview src="https://example.test/image.jpg" name="image.jpg" />);
    fireEvent.click(screen.getByRole('button', { name: 'Download' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('does not allow');
    expect(screen.queryByText('Download started')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Copy image' }));
    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent('unavailable in this browser'),
    );
  });

  it('aborts an unfinished download on close', async () => {
    const fetcher = vi.fn().mockImplementation(() => new Promise(() => {}));
    vi.stubGlobal('fetch', fetcher);
    const view = render(<ImagePreview src="https://example.test/image.jpg" name="image.jpg" />);
    fireEvent.click(screen.getByRole('button', { name: 'Download' }));
    expect(screen.getByRole('status')).toHaveTextContent('Preparing');
    view.unmount();
    expect(fetcher.mock.calls[0]?.[1].signal.aborted).toBe(true);
  });

  it('fetches remote images without credentials and rejects non-image or failed responses', async () => {
    const blob = new Blob(['PNG'], { type: 'image/png' });
    const fetcher = vi.fn().mockResolvedValue({ ok: true, blob: async () => blob });
    vi.stubGlobal('fetch', fetcher);
    const signal = new AbortController().signal;
    expect(await loadImageBlob('https://example.test/image.png', signal)).toBe(blob);
    expect(fetcher).toHaveBeenCalledWith(expect.any(String), {
      signal,
      credentials: 'omit',
      referrerPolicy: 'no-referrer',
    });
    expect(await imageAsPng(blob)).toBe(blob);
    fetcher.mockResolvedValue({ ok: false });
    await expect(loadImageBlob('https://example.test/missing.png', signal)).rejects.toThrow(
      'could not be downloaded',
    );
    fetcher.mockResolvedValue({
      ok: true,
      blob: async () => new Blob(['html'], { type: 'text/html' }),
    });
    await expect(loadImageBlob('https://example.test/misleading.png', signal)).rejects.toThrow(
      'did not return an image',
    );
  });
});
