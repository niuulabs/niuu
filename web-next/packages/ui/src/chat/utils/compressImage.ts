const MAX_DIMENSION = 1280;
const INITIAL_QUALITY = 0.85;
const FALLBACK_QUALITY = 0.5;
const TARGET_SIZE_BYTES = 300 * 1024; // 300 KB

/**
 * Compress an image File using canvas-based JPEG compression.
 * Resizes to fit within MAX_DIMENSION, then tries INITIAL_QUALITY.
 * If still above TARGET_SIZE_BYTES, falls back to FALLBACK_QUALITY.
 */
export async function compressImage(file: File): Promise<{ blob: Blob; previewUrl: string }> {
  const bitmap = await createImageBitmap(file);
  const { width, height } = bitmap;

  let targetWidth = width;
  let targetHeight = height;

  if (width > MAX_DIMENSION || height > MAX_DIMENSION) {
    const scale = MAX_DIMENSION / Math.max(width, height);
    targetWidth = Math.round(width * scale);
    targetHeight = Math.round(height * scale);
  }

  const canvas = new OffscreenCanvas(targetWidth, targetHeight);
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('Failed to get canvas context');

  ctx.drawImage(bitmap, 0, 0, targetWidth, targetHeight);
  bitmap.close();

  let blob = await canvas.convertToBlob({ type: 'image/jpeg', quality: INITIAL_QUALITY });

  if (blob.size > TARGET_SIZE_BYTES) {
    blob = await canvas.convertToBlob({ type: 'image/jpeg', quality: FALLBACK_QUALITY });
  }

  const previewUrl = URL.createObjectURL(blob);
  return { blob, previewUrl };
}
