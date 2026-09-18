/** Browser media operations: remote bytes require the image host's CORS permission. */
export async function loadImageBlob(url: string, signal: AbortSignal): Promise<Blob> {
  const response = await fetch(url, { signal, credentials: 'omit', referrerPolicy: 'no-referrer' });
  if (!response.ok) throw new Error('The image could not be downloaded.');
  const blob = await response.blob();
  if (!blob.type.startsWith('image/')) throw new Error('The link did not return an image.');
  return blob;
}

export async function imageAsPng(blob: Blob): Promise<Blob> {
  if (blob.type === 'image/png') return blob;
  const url = URL.createObjectURL(blob);
  try {
    const image = new Image();
    image.src = url;
    await image.decode();
    const canvas = document.createElement('canvas');
    canvas.width = image.naturalWidth;
    canvas.height = image.naturalHeight;
    const context = canvas.getContext('2d');
    if (!context) throw new Error('Image copying is unavailable in this browser.');
    context.drawImage(image, 0, 0);
    return await new Promise<Blob>((resolve, reject) =>
      canvas.toBlob(
        (png) => (png ? resolve(png) : reject(new Error('Could not copy this image.'))),
        'image/png',
      ),
    );
  } finally {
    URL.revokeObjectURL(url);
  }
}

export function downloadImageBlob(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = name;
  document.body.append(link);
  link.click();
  link.remove();
  // Give the browser time to start reading the object URL before releasing it.
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
