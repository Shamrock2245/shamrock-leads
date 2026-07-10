# Policy: GAS Web App URL Stability

> **Status:** Non-negotiable ecosystem rule  
> **Applies to:** leads · portal · bail-school · node-red · telegram · Wix · Netlify  
> **Owner notification:** Brendan (Wix Secrets Manager is outside agent-managed deploy paths)  
> **Canonical deploy ID (portal factory):** see `shamrock-bail-portal-site/.gas-config.json`

---

## Rule (short version)

1. **Keep the GAS URL the same.** Update **code + deployment version only**.
2. **Never create a brand-new Web App deployment** (new `/macros/s/…/exec` URL) unless the human owner explicitly orders it.
3. **If the GAS URL must change**, **stop and notify the human immediately** — they must update **Wix Secrets Manager** (and any other external systems agents do not control).

---

## Why this exists

The live Web App URL is wired into places **outside** a single `git push`:

| Consumer | Typical key / location |
|----------|------------------------|
| **Wix Secrets Manager** | `GAS_WEB_APP_URL` / `GAS_WEBHOOK_URL` (portal Velo / backend) |
| **Netlify** (bail-school) | `GAS_WEBHOOK_URL`, optional `NEXT_PUBLIC_GAS_URL` |
| **Hetzner / leads `.env`** | `GAS_WEB_APP_URL` |
| **Node-RED** | `GAS_WEBHOOK_URL` / flow HTTP nodes |
| **Telegram mini-apps / bookmarklets** | Hardcoded or env-backed `/exec` URLs |
| **GAS Script Properties** | `GAS_WEB_APP_URL` (self-reference / helpers) |

A silent URL change breaks intake, paperwork, school auth, and automations until every consumer is updated — and **Wix Secrets are not updated by clasp or Netlify deploy**.

---

## Correct workflow (URL stays the same)

```bash
# Portal / shared factory GAS
cd shamrock-bail-portal-site/backend-gas
npx @google/clasp push -f
# Re-deploy the EXISTING deployment ID (URL unchanged):
npx @google/clasp deploy -i <EXISTING_DEPLOYMENT_ID> -d "V### - short description"
```

- Prefer the deployment ID in `.gas-config.json` (`deploymentId`).
- Push updates code; **deploy with `-i <same id>`** updates that deployment’s executable version **without** minting a new URL.

### What agents may change freely

- `.js` / `.gs` source in `backend-gas/`
- Script Properties that are **not** the public Web App URL (e.g. `GAS_API_KEY`, template IDs)
- Netlify/VPS app code that **calls** GAS (as long as env still points at the same URL)

### What agents must not do without explicit human approval

- `Deploy → New deployment` in the Apps Script UI (new Web App)
- `clasp deploy` **without** `-i <existing id>` when that creates a new deployment URL
- Running `scripts/update-gas-url.sh <NEW_ID>` (or equivalent search-replace of `/macros/s/AKfyc…/exec`) as part of a “routine fix”
- “Helpful” updates that rewrite `GAS_WEBHOOK_URL` / `GAS_WEB_APP_URL` in env files to a different path

---

## If the URL must change (exception path)

Only when the owner says so (broken deployment, forced Google rotation, project migration):

1. **Stop.** Do not silently propagate a new URL across the monorepo.
2. **Notify the human in chat** with:
   - Old URL (or deployment ID)
   - New URL (or deployment ID)
   - Why a new deployment was required
   - Exact external checklist (below)
3. After approval, update in **one change window**:
   - [ ] Wix Secrets Manager → `GAS_WEB_APP_URL` / `GAS_WEBHOOK_URL`
   - [ ] Netlify → `GAS_WEBHOOK_URL` (+ `NEXT_PUBLIC_GAS_URL` if used)
   - [ ] Hetzner leads `.env` → `GAS_WEB_APP_URL`
   - [ ] Node-RED env / flows → `GAS_WEBHOOK_URL`
   - [ ] `.gas-config.json` + any intentional hardcoded clients (telegram, bookmarklets)
   - [ ] GAS Script Property `GAS_WEB_APP_URL` if set
4. Smoke-test: portal intake → GAS; school magic-link; one Node-RED GAS call.

**Human-only step:** Wix Secrets Manager. Agents cannot assume this was done.

---

## Naming map (same rule, different env names)

| Name in env / docs | Meaning |
|--------------------|---------|
| `GAS_WEB_APP_URL` | Leads / portal / Script Property — web app `/exec` URL |
| `GAS_WEBHOOK_URL` | School / Node-RED — same class of URL |
| `NEXT_PUBLIC_GAS_URL` | Optional browser-facing school helper (prefer server proxy) |

All of these must keep pointing at the **same stable deployment** unless the exception path ran and the human updated Wix.

---

## Related docs

- Ecosystem overview: `docs/ECOSYSTEM.md`
- Portal deploy rules: `shamrock-bail-portal-site/backend-gas/GAS_DEVELOPMENT_RULES.md`
- Portal config: `shamrock-bail-portal-site/.gas-config.json`
- Secrets checklist: `scripts/check_ecosystem_secrets.py`
