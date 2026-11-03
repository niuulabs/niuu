# NIUU — Continuity / Brand System v0.4 (concept)

**Promise:** A place where intelligent systems do real work.  
**Idea:** Work continues.  
**Personality:** Calm confidence.  
**Visual tone:** Precise, luminous, structured, dark but not oppressive.  
**Voice:** Clear, direct, non-hype, ambitious.

## The reasoning
A session ends, a model changes, a different agent takes over, or a person steps away. Work should not have to start again. NIUU connects goals, responsibilities, capabilities, decisions, knowledge and evidence across those transitions. The brand therefore uses *continuous paths*: distinct orbital trajectories sharing space, and a flowing field with branches that meet and rejoin. Not a literal sine wave, motorway, starfield or circuit-board. The goal is a quiet sense of durable movement.

## Mark and wordmark
- **Orbital symbol:** Two distinct Bézier paths with asymmetric intersection. Its continuous sweep represents progress and handoff. The visible crossing is intentional.
- **Wide NIUU lettering:** Original path-rendered SVG reconstruction of the approved geometric wide-spaced wordmark; it is intentionally not replaced with ordinary Inter. For primary lockups use the actual SVG.
- **Variants:** `niuu-orbit-gradient.svg` is hero scale; `niuu-orbit-flat.svg` for UI at 24–64 px; `niuu-orbit-mono.svg` for one-color use; `niuu-orbit-dark.svg` for light surfaces. Try minimum 24px but validate 16px favicon at real sizes.
- **Clear space:** at least 20% of mark diameter. Don't stretch, re-color as a rainbow, or overlay glow on small app icons.

## Palette (single family)
| Role | Hex | Use |
|---|---|---|
| Ink | `#071019` | Main canvas |
| Midnight | `#0B1722` | Depth, hero |
| Panel | `#10212D` | Functional work surfaces |
| Raised | `#182B38` | Selection and elevated surfaces |
| Divider | `#2B4351` | Rules and borders |
| Mist | `#EDF4F7` | Primary text |
| Secondary | `#A5B7C1` | Secondary text |
| Signal | `#86D0E8` | CTA, active selection and focus |

Signal should occupy a small percentage of the interface. Use green/amber/red only to communicate state (success/warning/error), not as product-brand colors. Keep the six products in the same family.

## Typography
- NIUU wordmark: SVG letter paths, wide engineered letterforms (do not substitute generic font text).
- Headlines: Inter Display Light 300, preferably 46–75px on marketing surfaces with careful line breaks and negative tracking.
- UI/body: Inter 400/500, 13–17px, comfortable reading contrast.
- Data / technical metadata: monospace at small sizes only.
- No font files included in this deliverable.

## Waves and motion
One brighter continuous path is supported by quiet companion lines. Points mark *actual transitions*; avoid random constellations or glowing towers. Backgrounds belong on hero, section openers, presentation/deck covers—rarely within dense product controls. Suggested motion: <=2px drift over 18 seconds, optional only, disabled for reduced motion. v0.4 ships static by default.

## Product family
1. NIUU Platform — Your environment
2. NIUU Workspaces — Where work happens
3. NIUU Agents — Who does the work
4. NIUU Forge — Build and execute
5. NIUU Memory — What is known
6. NIUU Observatory — What is happening

One stroke icon system, same Signal accent, predictable labels. Internal mythological names remain implementation details, not mandatory public concepts.

## Writing
Use claims you can demonstrate; the broad goal-centric NIUU OS vision should be described as a direction until implemented. Prefer “Work continues across agents and sessions” to empty AI hype. The interface examples are illustrative, not evidence of production functionality or measurements.

## Deployment notes / validation
`standalone.html` is independent of assets and can be opened offline; `index.html` is a copy of it. The `assets/` folder contains editable SVG masters and design tokens. Test in product at 16/24/32px, verify contrast in final UI, clear rights/licensing, perform trademark screening, and validate the wordmark and imagery with users before launch. This remains a concept, not a vetted registered mark.
