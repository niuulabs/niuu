import * as RadixSelect from '@radix-ui/react-select';
import { cn } from '../../utils/cn';
import { useField } from '../Field';
import type { Option } from '../option';

export type SelectOption = Option;

export interface SelectProps {
  options: SelectOption[];
  placeholder?: string;
  value?: string;
  defaultValue?: string;
  onValueChange?: (value: string) => void;
  disabled?: boolean;
  name?: string;
  required?: boolean;
  className?: string;
}

export function Select({
  options,
  placeholder = 'Select…',
  value,
  defaultValue,
  onValueChange,
  disabled,
  name,
  required,
  className,
}: SelectProps) {
  const { id, hintId, errorId, hasError } = useField();

  const describedBy = [hintId, errorId].filter(Boolean).join(' ') || undefined;

  return (
    <RadixSelect.Root
      value={value}
      defaultValue={defaultValue}
      onValueChange={onValueChange}
      disabled={disabled}
      name={name}
      required={required}
    >
      <RadixSelect.Trigger
        id={id}
        className={cn(
          'niuu-form-control niuu-select__trigger',
          hasError && 'niuu-form-control--error niuu-select__trigger--error',
          className,
        )}
        aria-invalid={hasError || undefined}
        aria-describedby={describedBy}
      >
        <RadixSelect.Value placeholder={placeholder} />
        <RadixSelect.Icon className="niuu-select__icon" aria-hidden="true">
          <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden="true">
            <path
              d="M2 4l4 4 4-4"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </RadixSelect.Icon>
      </RadixSelect.Trigger>
      <RadixSelect.Portal>
        <RadixSelect.Content className="niuu-select__content" position="popper" sideOffset={4}>
          <RadixSelect.Viewport className="niuu-select__viewport">
            {options.map((option) => (
              <RadixSelect.Item
                key={option.value}
                value={option.value}
                disabled={option.disabled}
                className="niuu-select__item"
              >
                <RadixSelect.ItemIndicator className="niuu-select__item-indicator">
                  <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden="true">
                    <path
                      d="M2 6l3 3 5-5"
                      stroke="currentColor"
                      strokeWidth="1.5"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                  </svg>
                </RadixSelect.ItemIndicator>
                <RadixSelect.ItemText>{option.label}</RadixSelect.ItemText>
              </RadixSelect.Item>
            ))}
          </RadixSelect.Viewport>
        </RadixSelect.Content>
      </RadixSelect.Portal>
    </RadixSelect.Root>
  );
}
