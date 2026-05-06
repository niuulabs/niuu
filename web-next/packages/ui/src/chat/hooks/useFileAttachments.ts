import { useCallback, useEffect, useRef, useState } from 'react';
import { compressImage } from '../utils/compressImage';

export interface FileAttachment {
  id: string;
  file: File;
  name: string;
  compressed: Blob | null;
  previewUrl: string | null;
}

interface UseFileAttachmentsReturn {
  attachments: FileAttachment[];
  isDragging: boolean;
  addFiles: (fileList: FileList) => void;
  removeAttachment: (id: string) => void;
  clearAttachments: () => void;
  handleDragOver: (e: React.DragEvent) => void;
  handleDragLeave: (e: React.DragEvent) => void;
  handleDrop: (e: React.DragEvent) => void;
  handlePaste: (e: React.ClipboardEvent) => void;
}

export function useFileAttachments(): UseFileAttachmentsReturn {
  const [attachments, setAttachments] = useState<FileAttachment[]>([]);
  const [isDragging, setIsDragging] = useState(false);
  const previewUrlsRef = useRef<string[]>([]);

  useEffect(() => {
    const urls = previewUrlsRef;
    return () => {
      for (const url of urls.current) {
        URL.revokeObjectURL(url);
      }
    };
  }, []);

  const processFile = useCallback(async (file: File): Promise<FileAttachment> => {
    const id = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    if (!file.type.startsWith('image/')) {
      return { id, file, name: file.name, compressed: null, previewUrl: null };
    }
    try {
      const { blob, previewUrl } = await compressImage(file);
      previewUrlsRef.current.push(previewUrl);
      return { id, file, name: file.name, compressed: blob, previewUrl };
    } catch {
      return { id, file, name: file.name, compressed: null, previewUrl: null };
    }
  }, []);

  const addFiles = useCallback(
    (fileList: FileList) => {
      const filesArray = Array.from(fileList);
      Promise.all(filesArray.map(processFile)).then((newAttachments) => {
        setAttachments((prev) => [...prev, ...newAttachments]);
      });
    },
    [processFile],
  );

  const removeAttachment = useCallback((id: string) => {
    setAttachments((prev) => {
      const att = prev.find((a) => a.id === id);
      if (att?.previewUrl) URL.revokeObjectURL(att.previewUrl);
      return prev.filter((a) => a.id !== id);
    });
  }, []);

  const clearAttachments = useCallback(() => {
    setAttachments((prev) => {
      for (const att of prev) {
        if (att.previewUrl) URL.revokeObjectURL(att.previewUrl);
      }
      return [];
    });
  }, []);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
  }, []);

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setIsDragging(false);
      if (e.dataTransfer.files.length > 0) {
        addFiles(e.dataTransfer.files);
      }
    },
    [addFiles],
  );

  const handlePaste = useCallback(
    (e: React.ClipboardEvent) => {
      const items = Array.from(e.clipboardData.items);
      const imageItems = items.filter(
        (item) => item.kind === 'file' && item.type.startsWith('image/'),
      );
      if (imageItems.length === 0) return;
      e.preventDefault();
      const dt = new DataTransfer();
      for (const item of imageItems) {
        const file = item.getAsFile();
        if (file) dt.items.add(file);
      }
      if (dt.files.length > 0) addFiles(dt.files);
    },
    [addFiles],
  );

  return {
    attachments,
    isDragging,
    addFiles,
    removeAttachment,
    clearAttachments,
    handleDragOver,
    handleDragLeave,
    handleDrop,
    handlePaste,
  };
}
