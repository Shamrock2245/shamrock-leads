---
name: wix-app-builder
description: "Build Wix CLI app extensions — dashboard pages, modals, plugins, widgets, backend APIs, events, service plugins, data collections. Use when building ANY feature or extension for a Wix CLI app. Covers the full extension type decision tree, implementation workflow, and validation checklist."
source: "https://github.com/wix/skills/tree/main/skills/wix-app"
compatibility: Requires Wix CLI development environment.
---

# Wix App Builder

Build extensions for Wix CLI applications. Covers all extension types: dashboard pages, modals, plugins, menu plugins, custom element widgets, Editor React components, site plugins, embedded scripts, backend APIs, events, service plugins, and data collections.

## Quick Decision Helper

1. **What are you building?**
   - Admin interface → Dashboard Extensions
   - Backend logic → Backend Extensions
   - Data storage / CMS collections → Data Collection
   - Editor React component → Site Extensions

2. **Who will see it?**
   - Admin users only → Dashboard Extensions
   - Site visitors → Site Extensions
   - Server-side only → Backend Extensions

3. **Where will it appear?**
   - Dashboard sidebar/page → Dashboard Page or Modal
   - Existing Wix app dashboard (widget) → Dashboard Plugin
   - Anywhere on site → Custom Element Widget
   - During business flow → Service Plugin
   - After event occurs → Backend Event Extension

## Extension Types

| Extension Type | Category | Visibility | Use When |
|----------------|----------|------------|----------|
| Dashboard Page | Dashboard | Admin only | Full admin pages |
| Dashboard Modal | Dashboard | Admin only | Popup dialogs |
| Dashboard Plugin | Dashboard | Admin only | Extend Wix app dashboards |
| Service Plugin | Backend | Server-side | Customize business flows |
| Backend Event | Backend | Server-side | React to events |
| Backend API | Backend | API | Custom HTTP handlers |
| Data Collection | Backend | Data | CMS collections for app data |
| Custom Element Widget | Site | Public | Standalone widgets |
| Site Plugin | Site | Public | Extend Wix business solutions |
| Embedded Script | Site | Public | Inject scripts/analytics |

**Key constraint:** Dashboard Pages cannot use `<Modal />`; use a separate Dashboard Modal and `dashboard.openModal()`.

## Mandatory Workflow Checklist

1. **Determine extension type(s) needed**
2. **Read extension reference file(s)** for the chosen type(s)
3. **Check API references; use MCP discovery only for gaps**
4. **Implement all extensions**
   - All files created
   - Extension(s) registered in `extensions.ts`
   - Invoke `wix-design-system` skill before writing first `.tsx`/`.jsx`
5. **Run validation** (deps → TypeScript → build → preview)
6. **Collect and present ALL manual action items**

## Extension Registration

```typescript
import { app } from "@wix/astro/builders";
import { dashboardpageMyPage } from "./extensions/dashboard/pages/my-page/extensions.ts";

export default app()
  .use(dashboardpageMyPage);
```

## Validation Steps

1. Package installation (detect package manager, run install)
2. TypeScript compilation: `npx tsc --noEmit`
3. Build: `npx wix build`
4. Preview: `npx wix preview`

## ShamrockLeads Context

Our Wix portal (`shamrock-bail-portal-site`) uses Wix Velo, not the CLI app pattern. However, this skill is essential for:
- Building future Wix Marketplace apps
- Understanding the extension architecture that powers Wix internals
- Creating custom backend APIs and service plugins
- Data Collection extensions for CMS integration

## Anti-Patterns

| ❌ Wrong | ✅ Correct |
|----------|-----------|
| Implementing without reading reference | Always read the relevant reference first |
| Using MCP discovery without checking refs | Check reference files first |
| Reporting done without validation | Always run validation at the end |
| Letting manual items get buried | Aggregate all manual steps at the end |
