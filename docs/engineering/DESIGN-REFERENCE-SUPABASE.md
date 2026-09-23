# Design reference: Supabase dashboard

The reference screenshots were captured by the user from their production account
and are committed under `docs/design/supabase-reference/`. They inform the
platform layer's organizations and projects screens only, as material for layout
and hierarchy.

Per-environment administration is not modelled on Studio: it is served as the
original upstream Studio itself, pinned and attributed like the other upstream
components. See [the integration specification](STUDIO-INTEGRATION.md). Do not copy
Studio assets, logos or code into our own platform layer; the upstream image is
served unmodified instead, under its own license.

## Reference screenshots

| File | Screen | What to learn from it |
|---|---|---|
| `organizations.png` | Organizations list | Empty, calm landing page for a top-level entity |
| `projects.png` | Projects list | Card grid with metadata, filters and view toggles |
| `org-settings.png` | Organization settings | Sectioned settings page with danger zone |

## Patterns to follow

### Layout
- Permanent icon rail on the far left (top-level navigation, active item
  highlighted), collapsible to icons only.
- Top bar: breadcrumb with entity switcher (org selector with plan badge),
  global search with `Ctrl K` hint, help and feedback on the right.
- Content is left-aligned, generous whitespace, max-width comfortable reading
  column for settings pages.

### Cards and lists
- Entity collections (organizations, projects) are cards in a responsive grid:
  rounded corners, subtle border, dark surface slightly lighter than the page
  background, generous padding.
- Card anatomy: icon, name (bold), secondary line (`Plan · N projects` or
  `Region | provider`), small monospace badges (MICRO, NANO) and a slug chip.
- Overflow actions (rename, delete) live behind a three-dot menu on the card,
  never inline buttons.

### Toolbars
- Above every list: search input on the left, filter dropdowns (Status) and a
  sort control, primary action button (`New organization` / `New project`) on
  the far right, in brand green with a `+` icon.
- Grid/list view toggle for collections.

### Color and type
- Near-black page background, dark raised surfaces for cards and panels.
- One brand accent (green) used sparingly: primary buttons, links, active
  states, plan badge.
- Destructive actions use red text on dark red surface, isolated in a clearly
  labeled "Danger zone" section at the bottom of settings, with a warning icon
  and a backup reminder sentence before the destructive button.

### Settings pages
- Second-level left navigation inside settings, grouped under uppercase section
  labels (CONFIGURATION, CONNECTIONS, COMPLIANCE).
- Each setting block: title, explanatory paragraph, then control. Radio groups
  show a full explanatory sentence per option, not just a label.
- Cancel and Save are bottom-right of each block; blocks save independently.

### UX behaviors worth mirroring
- Global command search (`Ctrl K`) reachable everywhere.
- Plan/tier badge always visible next to the entity it applies to.
- Copy buttons next to identifiers and slugs.
- Destructive flows always state consequences and recovery (backup) first.

## Hierarchy mapping

Supabase shows organization > project. sbarbase is
installation > organization > project > environment. Map the screens:

- Organizations list -> sbarbase organizations.
- Projects list -> sbarbase projects (add environment count/selector in card).
- Organization settings -> organization/project settings sections.
- Environment settings -> not ours. The platform layer links into that
  environment's Studio for anything inside the environment.
