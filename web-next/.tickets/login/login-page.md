# Login Page — Visual Parity with web2

**Visual test:** `e2e/visual/login.visual.spec.ts` → `login page matches web2`
**Status:** PASS (≤5% pixel diff) — but has missing UI elements
**Web2 baseline:** `e2e/__screenshots__/web2/login/login-page.png`
**Web2 source:** `web2/niuu_handoff/niuu_login/design/page.jsx`, `styles.css`, `logos.jsx`, `ambient.jsx`
**Web-next source:** `packages/plugin-login/src/ui/LoginPage.tsx`, `LoginPage.css`, `ambient/`

---

## Summary

The page passes the visual test at 5% tolerance, but is missing required UI elements:
the OAuth provider buttons (passkey primary + GitHub/Google secondary) and the
"no account? request access" footer. These must be added to match web2.

---

## Required changes

### 1. Add OAuth provider buttons

**Web2 spec** (`page.jsx` lines 68–87):

```jsx
<div className="niuu-auth">
  <button className="niuu-btn primary">
    <LockIcon />
    <span>Continue with passkey</span>
    <span className="niuu-kbd mono">↵</span>
  </button>
  <div className="niuu-oauth-row">
    <button className="niuu-btn ghost">
      <GithubIcon />
      <span>GitHub</span>
    </button>
    <button className="niuu-btn ghost">
      <GoogleIcon />
      <span>Google</span>
    </button>
  </div>
</div>
```

**Web-next currently** (`LoginPage.tsx` lines 65–79): Single "Sign in" button only.

**What to do:**

1. **Rename the primary CTA** from "Sign in" → "Continue with passkey" and add
   the keyboard hint badge (`↵`).

2. **Add an OAuth row** below the primary button with two ghost-style buttons:
   - GitHub (with GitHub SVG icon)
   - Google (with Google SVG icon)

3. **CSS for `.login-page__oauth-row`** (add to `LoginPage.css`):

   ```css
   .login-page__oauth-row {
     display: flex;
     gap: 10px;
   }
   ```

4. **CSS for `.login-page__btn--ghost`** (add to `LoginPage.css`):

   ```css
   .login-page__btn--ghost {
     flex: 1;
     background: var(--color-bg-tertiary);
     color: var(--color-text-primary);
     border: 1px solid var(--color-border);
   }
   .login-page__btn--ghost:hover:not(:disabled) {
     background: color-mix(in srgb, var(--brand-500) 10%, var(--color-bg-tertiary));
     border-color: color-mix(in srgb, var(--brand-500) 25%, var(--color-border));
   }
   ```

5. **CSS for `.login-page__kbd`** (keyboard hint on passkey button):

   ```css
   .login-page__kbd {
     margin-left: auto;
     font-size: var(--text-xs);
     font-family: var(--font-mono);
     padding: 0 5px;
     border: 1px solid color-mix(in srgb, var(--_btn-contrast) 30%, transparent);
     border-radius: 3px;
     color: var(--_btn-contrast);
     opacity: 0.7;
   }
   ```

6. **SVG icons:** Create `GithubIcon.tsx` and `GoogleIcon.tsx` in
   `packages/plugin-login/src/ui/icons/`. Reference the SVG paths from
   `web2/niuu_handoff/niuu_login/design/page.jsx` lines 14–36.

7. **Auth behavior:** All three buttons call the same `login()` from `useAuth()`.
   The visual distinction (passkey vs GitHub vs Google) is cosmetic for now —
   the IDP handles provider routing. If provider-specific OIDC flows are needed
   later, extend `useAuth().login(provider?: string)`.

**Files to modify:**

- `packages/plugin-login/src/ui/LoginPage.tsx` — add OAuth row JSX
- `packages/plugin-login/src/ui/LoginPage.css` — add ghost button + kbd styles
- Create `packages/plugin-login/src/ui/icons/GithubIcon.tsx`
- Create `packages/plugin-login/src/ui/icons/GoogleIcon.tsx`

---

### 2. Add "no account? request access" footer

**Web2 spec** (`page.jsx` lines 89–92):

```jsx
<div className="niuu-foot mono">
  <span className="dim">no account?</span>
  <a href="#" className="niuu-link">
    request access
  </a>
</div>
```

**What to do:**

1. Add footer JSX below the auth section in `LoginPage.tsx`:

   ```tsx
   <div className="login-page__foot login-page__mono">
     <span className="login-page__foot-dim">no account?</span>
     <a href="#" className="login-page__link">
       request access
     </a>
   </div>
   ```

2. **CSS** (add to `LoginPage.css`):

   ```css
   .login-page__foot {
     display: flex;
     gap: 6px;
     align-items: center;
     font-size: var(--text-xs);
     color: var(--color-text-muted);
   }
   .login-page__foot-dim {
     color: var(--color-text-muted);
   }
   .login-page__link {
     color: var(--brand-300);
     text-decoration: none;
     transition: color 120ms;
   }
   .login-page__link:hover {
     color: var(--brand-200);
     text-decoration: underline;
   }
   ```

3. **Behavior:** The `href` should point to a request-access route or external
   form. For now, use `#` and wire later.

**Files to modify:**

- `packages/plugin-login/src/ui/LoginPage.tsx` — add footer JSX
- `packages/plugin-login/src/ui/LoginPage.css` — add footer + link styles

---

### 3. Add build metadata version string

**Web2 spec** (`page.jsx` line 60):

```jsx
niuu · build 2026.04.18-7f3a2c · valaskjálf
```

**Web-next currently:** Shows only `niuu` (no version or realm).

**What to do:**

- Read version and realm from `config.json` (via `useConfig()`) or build-time env
- Render as: `niuu · build ${version} · ${realm}`
- If config has no version, fall back to just `niuu` (current behavior)

**Files to modify:**

- `packages/plugin-login/src/ui/LoginPage.tsx` — update build info rendering

---

## What to keep as-is (web-next improvements over web2)

| Element           | Web-next behavior                            | Why keep                               |
| ----------------- | -------------------------------------------- | -------------------------------------- |
| Error handling    | OIDC error display with `role="alert"`       | Production requirement — web2 had none |
| Loading state     | Spinner + disabled button + "redirecting…"   | Production requirement — web2 had none |
| Motion preference | `useReducedMotion()` in all ambient variants | Accessibility requirement              |
| ARIA labels       | `aria-label` on all interactive elements     | Accessibility requirement              |
| `data-testid`     | On all key elements                          | Testing infrastructure                 |
| CSS tokens        | `var(--text-xs)` instead of hardcoded `11px` | Better maintainability                 |
| Tweaks panel      | Removed                                      | Design-time tool, not for end users    |

---

## Shared components

All login-specific — nothing to promote to `@niuulabs/ui`.

---

## Acceptance criteria

1. Login page shows passkey button (primary), GitHub + Google buttons (ghost)
2. "no account? request access" footer visible below auth section
3. Build metadata shows version + realm when available
4. Visual test `login page matches web2` passes with ≤5% pixel diff
5. Existing error handling, loading state, and accessibility preserved
6. All new elements have `data-testid` attributes
7. Unit tests cover new button rendering and footer
