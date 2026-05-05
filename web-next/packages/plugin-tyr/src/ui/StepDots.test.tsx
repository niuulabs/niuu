import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { StepDots } from './StepDots';
import { PLAN_STEPS } from '../domain/plan';

describe('StepDots', () => {
  it('renders all 5 step labels', () => {
    render(<StepDots steps={PLAN_STEPS} current="prompt" />);
    expect(screen.getByText('Describe')).toBeInTheDocument();
    expect(screen.getByText('Clarify')).toBeInTheDocument();
    expect(screen.getByText('Decompose')).toBeInTheDocument();
    expect(screen.getByText('Review')).toBeInTheDocument();
    expect(screen.getByText('Launch')).toBeInTheDocument();
  });

  it('marks the current step with aria-current="step"', () => {
    render(<StepDots steps={PLAN_STEPS} current="questions" />);
    const currentStep = screen.getByRole('navigation').querySelector('[aria-current="step"]');
    expect(currentStep).toBeInTheDocument();
  });

  it('does not set aria-current on non-active steps', () => {
    render(<StepDots steps={PLAN_STEPS} current="prompt" />);
    const nav = screen.getByRole('navigation');
    const currentSteps = nav.querySelectorAll('[aria-current="step"]');
    expect(currentSteps).toHaveLength(1);
  });

  it('has accessible navigation label', () => {
    render(<StepDots steps={PLAN_STEPS} current="raiding" />);
    expect(screen.getByRole('navigation', { name: 'Plan wizard steps' })).toBeInTheDocument();
  });

  it('renders dot indicators with aria-labels', () => {
    render(<StepDots steps={PLAN_STEPS} current="raiding" />);
    // raiding is current, prompt and questions are complete, draft and approved are pending
    expect(screen.getByRole('img', { name: /Describe: complete/ })).toBeInTheDocument();
    expect(screen.getByRole('img', { name: /Clarify: complete/ })).toBeInTheDocument();
    expect(screen.getByRole('img', { name: /Decompose: current/ })).toBeInTheDocument();
    expect(screen.getByRole('img', { name: /Review: pending/ })).toBeInTheDocument();
    expect(screen.getByRole('img', { name: /Launch: pending/ })).toBeInTheDocument();
  });

  it('shows all steps as pending (except first) when on prompt', () => {
    render(<StepDots steps={PLAN_STEPS} current="prompt" />);
    expect(screen.getByRole('img', { name: /Describe: current/ })).toBeInTheDocument();
    expect(screen.getByRole('img', { name: /Clarify: pending/ })).toBeInTheDocument();
  });

  it('marks all prior steps as complete when on approved', () => {
    render(<StepDots steps={PLAN_STEPS} current="approved" />);
    expect(screen.getByRole('img', { name: /Describe: complete/ })).toBeInTheDocument();
    expect(screen.getByRole('img', { name: /Clarify: complete/ })).toBeInTheDocument();
    expect(screen.getByRole('img', { name: /Decompose: complete/ })).toBeInTheDocument();
    expect(screen.getByRole('img', { name: /Review: complete/ })).toBeInTheDocument();
    expect(screen.getByRole('img', { name: /Launch: current/ })).toBeInTheDocument();
  });
});
