import { useQuery } from '@tanstack/react-query';
import type { IBifrostService } from '@niuulabs/plugin-bifrost';
import { useService } from '@niuulabs/plugin-sdk';
import { formatModelOption, WizardSelect } from '@niuulabs/plugin-volundr';

interface ResidentModelSelectProps {
  allowedModels: string[];
  modelPrefix: string;
  value: string;
  onChange: (value: string) => void;
  testId: string;
}

export function ResidentModelSelect({
  allowedModels,
  modelPrefix,
  value,
  onChange,
  testId,
}: ResidentModelSelectProps) {
  const bifrost = useService<IBifrostService>('bifrost');
  const modelsQuery = useQuery({
    queryKey: ['bifrost', 'models'],
    queryFn: () => bifrost.listModels(),
  });
  const models = new Map((modelsQuery.data ?? []).map((model) => [model.id, model]));
  const options = allowedModels.map((modelId) => {
    const catalogId =
      modelPrefix && modelId.startsWith(modelPrefix) ? modelId.slice(modelPrefix.length) : modelId;
    return {
      value: modelId,
      label: formatModelOption(modelId, models.get(catalogId)),
    };
  });

  return (
    <WizardSelect
      options={options}
      value={value}
      onChange={onChange}
      placeholder="Select model"
      testId={testId}
    />
  );
}
