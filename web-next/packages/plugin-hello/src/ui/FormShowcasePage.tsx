import { useState, type ChangeEvent, type FormEvent } from 'react';
import {
  Field,
  Input,
  Textarea,
  Select,
  Combobox,
  ValidationSummary,
  type ValidationError,
} from '@niuulabs/ui';

const ROLE_OPTIONS = [
  { value: 'admin', label: 'Administrator' },
  { value: 'editor', label: 'Editor' },
  { value: 'viewer', label: 'Viewer' },
];

const TEAM_OPTIONS = [
  { value: 'platform', label: 'Platform' },
  { value: 'product', label: 'Product' },
  { value: 'data', label: 'Data' },
  { value: 'security', label: 'Security' },
  { value: 'design', label: 'Design (unavailable)', disabled: true },
];

interface FormValues {
  name: string;
  email: string;
  bio: string;
  role: string;
  team: string;
}

function validate(values: FormValues): ValidationError[] {
  const errors: ValidationError[] = [];

  if (!values.name.trim()) {
    errors.push({ id: 'form-name', label: 'Full name', message: 'Full name is required' });
  }

  if (!values.email.trim()) {
    errors.push({ id: 'form-email', label: 'Email', message: 'Email is required' });
  } else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(values.email)) {
    errors.push({ id: 'form-email', label: 'Email', message: 'Enter a valid email address' });
  }

  if (!values.bio.trim()) {
    errors.push({ id: 'form-bio', label: 'Bio', message: 'Bio is required' });
  }

  if (!values.role) {
    errors.push({ id: 'form-role', label: 'Role', message: 'Please select a role' });
  }

  if (!values.team) {
    errors.push({ id: 'form-team', label: 'Team', message: 'Please select a team' });
  }

  return errors;
}

export function FormShowcasePage() {
  const [values, setValues] = useState<FormValues>({
    name: '',
    email: '',
    bio: '',
    role: '',
    team: '',
  });
  const [errors, setErrors] = useState<ValidationError[]>([]);
  const [submitted, setSubmitted] = useState(false);

  function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const validationErrors = validate(values);
    setErrors(validationErrors);

    if (validationErrors.length === 0) {
      setSubmitted(true);
    }
  }

  if (submitted) {
    return (
      <div className="niuu-p-8">
        <h1>Form submitted successfully!</h1>
        <button
          type="button"
          onClick={() => {
            setSubmitted(false);
            setValues({ name: '', email: '', bio: '', role: '', team: '' });
            setErrors([]);
          }}
        >
          Reset
        </button>
      </div>
    );
  }

  return (
    <div className="niuu-p-8 niuu-max-w-lg">
      <h1>Form Showcase</h1>
      <p>Submit the form with empty fields to see validation.</p>

      {errors.length > 0 && (
        <div className="niuu-mb-6">
          <ValidationSummary errors={errors} />
        </div>
      )}

      <form onSubmit={handleSubmit} noValidate>
        <div className="niuu-flex niuu-flex-col niuu-gap-4">
          <Field
            id="form-name"
            label="Full name"
            required
            error={errors.find((e) => e.id === 'form-name')?.message}
          >
            <Input
              value={values.name}
              onChange={(e: ChangeEvent<HTMLInputElement>) =>
                setValues((v) => ({ ...v, name: e.target.value }))
              }
              placeholder="Jane Doe"
            />
          </Field>

          <Field
            id="form-email"
            label="Email"
            required
            hint="We'll use this to contact you"
            error={errors.find((e) => e.id === 'form-email')?.message}
          >
            <Input
              type="email"
              value={values.email}
              onChange={(e: ChangeEvent<HTMLInputElement>) =>
                setValues((v) => ({ ...v, email: e.target.value }))
              }
              placeholder="jane@example.com"
            />
          </Field>

          <Field
            id="form-bio"
            label="Bio"
            required
            hint="Tell us a bit about yourself"
            error={errors.find((e) => e.id === 'form-bio')?.message}
          >
            <Textarea
              value={values.bio}
              onChange={(e: ChangeEvent<HTMLTextAreaElement>) =>
                setValues((v) => ({ ...v, bio: e.target.value }))
              }
              placeholder="I'm a software engineer who loves…"
              rows={3}
            />
          </Field>

          <Field
            id="form-role"
            label="Role"
            required
            error={errors.find((e) => e.id === 'form-role')?.message}
          >
            <Select
              options={ROLE_OPTIONS}
              value={values.role}
              onValueChange={(val: string) => setValues((v) => ({ ...v, role: val }))}
              placeholder="Select a role…"
            />
          </Field>

          <Field
            id="form-team"
            label="Team"
            required
            error={errors.find((e) => e.id === 'form-team')?.message}
          >
            <Combobox
              options={TEAM_OPTIONS}
              value={values.team}
              onValueChange={(val: string) => setValues((v) => ({ ...v, team: val }))}
              placeholder="Search teams…"
            />
          </Field>

          <button type="submit">Submit</button>
        </div>
      </form>
    </div>
  );
}
