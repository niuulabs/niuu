import type { WorkflowExport } from '../ports';

export function readWorkflowFile(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error ?? new Error(`Could not read ${file.name}.`));
    reader.onload = () => {
      if (typeof reader.result !== 'string') {
        reject(new Error(`Could not encode ${file.name}.`));
        return;
      }
      const separator = reader.result.indexOf(',');
      resolve(separator >= 0 ? reader.result.slice(separator + 1) : reader.result);
    };
    reader.readAsDataURL(file);
  });
}

export function downloadWorkflowFile(file: WorkflowExport): void {
  const url = URL.createObjectURL(file.data);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = file.filename;
  anchor.click();
  URL.revokeObjectURL(url);
}
