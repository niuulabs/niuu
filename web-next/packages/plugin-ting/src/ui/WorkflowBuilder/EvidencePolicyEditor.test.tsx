import { useState } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { EvidencePolicyEditor, type WorkflowEvidencePolicy } from './EvidencePolicyEditor';

function Harness({ initial }: { initial: WorkflowEvidencePolicy }) {
  const [policy, setPolicy] = useState(initial);
  return (
    <>
      <EvidencePolicyEditor policy={policy} onChange={setPolicy} />
      <output aria-label="Evidence policy value">{JSON.stringify(policy)}</output>
    </>
  );
}

describe('EvidencePolicyEditor', () => {
  it('round-trips result, review, and check trust requirements immutably', () => {
    const initial: WorkflowEvidencePolicy = {
      required_result_contract_ids: ['unit-tests'],
      result_producers: { 'unit-tests': ['ci-old'] },
      required_review_roles: ['privacy'],
      review_producers: { privacy: ['review-old'] },
      required_check_names: [],
      require_checks: false,
      check_producers: [],
    };
    const original = structuredClone(initial);
    render(<Harness initial={initial} />);

    fireEvent.change(screen.getByLabelText('Result contract 1'), {
      target: { value: 'artifact-contract' },
    });
    fireEvent.change(screen.getByLabelText('Trusted producers for result contract 1'), {
      target: { value: 'builder-a, builder-b, builder-a' },
    });
    fireEvent.change(screen.getByLabelText('Reviewer role 1'), {
      target: { value: 'data-governance' },
    });
    fireEvent.change(screen.getByLabelText('Trusted producers for reviewer role 1'), {
      target: { value: 'reviewer-a, reviewer-b' },
    });
    fireEvent.click(screen.getByLabelText('Require checks receipt'));
    fireEvent.change(screen.getByLabelText('Named checks'), {
      target: { value: 'policy-scan, integration' },
    });
    fireEvent.change(screen.getByLabelText('Trusted check producers'), {
      target: { value: 'ci-primary, scanner-prod' },
    });

    const policy = JSON.parse(screen.getByLabelText('Evidence policy value').textContent ?? '{}');
    expect(policy).toEqual({
      required_result_contract_ids: ['artifact-contract'],
      result_producers: { 'artifact-contract': ['builder-a', 'builder-b'] },
      required_review_roles: ['data-governance'],
      review_producers: { 'data-governance': ['reviewer-a', 'reviewer-b'] },
      required_check_names: ['policy-scan', 'integration'],
      require_checks: true,
      check_producers: ['ci-primary', 'scanner-prod'],
    });
    expect(initial).toEqual(original);
  });
});
