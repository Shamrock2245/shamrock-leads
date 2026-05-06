---
name: wix-design-system
description: "Wix Design System (WDS) component reference. Use when building UI with @wix/design-system, choosing components, checking props and examples, or writing tests. Covers component lookup, props, examples, testkits, icons, and spacing tokens."
source: "https://github.com/wix/skills/tree/main/skills/wix-design-system"
compatibility: Requires @wix/design-system npm package.
---

# WDS Documentation Navigator

**Prerequisite:** `@wix/design-system` must be installed.

```bash
npm i @wix/design-system
```

## Helper Script

This skill bundles `scripts/wds.cjs` for focused lookups:

```bash
WDS="<this-skill-dir>/scripts/wds.cjs"

node $WDS search <keyword>                 # Find components by keyword
node $WDS component <Name>                 # Get props + example list
node $WDS components <Name1> <Name2>...    # Batch component lookup
node $WDS example <Name> "<ExampleName>"   # Get a specific example
node $WDS testkit <Name> [method]          # Get testkit imports + driver API
node $WDS icons <query>                    # Search for icons
```

## Quick Component Mapping

| Design Element | WDS Component | Notes |
|---------------|---------------|-------|
| Rectangle/container | `<Box>` | Layout wrapper |
| Text button | `<TextButton>` | Secondary actions |
| Input with label | `<FormField>` + `<Input>` | Wrap inputs |
| Toggle | `<ToggleSwitch>` | On/off settings |
| Modal | `<Modal>` + `<CustomModalLayout>` | Use together |
| Grid | `<Layout>` + `<Cell>` | Responsive |

## Spacing (px to SP conversion)

| Token | Classic | Studio |
|-------|---------|--------|
| `SP1` | 6px | 4px |
| `SP2` | 12px | 8px |
| `SP3` | 18px | 12px |
| `SP4` | 24px | 16px |
| `SP5` | 30px | 20px |
| `SP6` | 36px | 24px |

```tsx
<Box gap="SP2" padding="SP3">
```

Only use SP tokens for `gap`, `padding`, `margin` — not for width/height.

## Imports

```tsx
import { Button, Card, Image } from "@wix/design-system";
import { Add, Edit, Delete } from "@wix/wix-ui-icons-common";
```

## Fallback: Direct File Access

If the script is unavailable, docs are at `node_modules/@wix/design-system/dist/docs/`:

- `components.md` — component catalog (~978 lines, grep only)
- `components/{Name}Props.md` — props per component
- `components/{Name}Examples.md` — examples per component
- `components/{Name}Testkit.md` — testkit imports + driver API
- `icons.md` — icon catalog (~818 lines, grep only)

Don't read these files fully. Grep for keywords, then read specific sections.
