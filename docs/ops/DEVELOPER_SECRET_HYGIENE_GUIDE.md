# Developer Secret Hygiene & Credential Protection Guide

> **Ecosystem Security Standard:** Shamrock Bail Bonds Engineering  
> **Status:** 🟢 **Active Standard** · Integrates with `scripts/scan_secrets.py` and `scripts/check_ecosystem_secrets.py`  
> **Policy Guard:** **Prevention does NOT substitute for historical rotation.** Checklist item **C3** remains open until vendor rotations are approved and executed by Brendan.

---

## 1. Core Directives

1. **Zero Raw Secrets in Source Control:** No API keys, webhook signing secrets, database credentials, OAuth refresh tokens, private keys, or passwords may ever be committed to git repositories, test fixtures, documentation, or commit messages.
2. **Deterministic Redacted Detection:** All automated scanners and error handlers must mask credentials. Never echo matched secrets or reconstructible fragments (`sk-proj-**** (length: 45)`).
3. **Truthful Environment Reporting:** A clean local checkout that lacks production `.env` files or sibling repositories must report `[UNVERIFIED / NOT-PROVEN]` rather than returning a deceptive green pass.

---

## 2. Where Secrets Live Across Shamrock Surfaces

| Surface | Allowed Secret Storage | Prohibited Storage | Safe Local Reference |
|---|---|---|---|
| **Super CRM (Hetzner VPS)** | Local `.env` (gitignored, file permission `600`) | Tracked Python scripts, Dockerfiles, public git branches | `os.getenv("SECRET_NAME")` |
| **Central GAS Factory** | Google Apps Script **Script Properties** | `Code.js`, `SetProperties.gs`, committed `.gs` files | `PropertiesService.getScriptProperties().getProperty("SECRET_NAME")` |
| **Wix Velo Frontend** | **Wix Secrets Manager** (backend jsw / web modules) | Frontend page scripts (`masterPage.js`, `pages/*.js`), public git | `import { getSecret } from 'wix-secrets-backend';` |
| **Telegram Mini-Apps** | **Netlify Environment Variables** (Scope: Builds & Functions) | `app.js`, HTML markup, client bundle scripts | `process.env.SECRET_NAME` in Netlify Edge Functions |
| **Bail School LMS** | Dedicated `.env.local` / Netlify Environment | Committed Next.js page components or public configs | `process.env.SECRET_NAME` |

---

## 3. Safe Patterns for Tests, Fixtures, and Documentation

When writing documentation, markdown guides, examples, or test fixtures, **always** use standard placeholder patterns:

### Allowed Safe Placeholders:
- `REPLACE_WITH_OPENAI_KEY`
- `<YOUR_DOCUSEAL_API_KEY>`
- `your_api_key_here`
- `mock_token_123` / `test_secret_val`
- `masked` / `***` / `...`
- Non-secret fingerprint prefixes: `fp:a61e521349`, `sha256:...`, `corr_...`

### Prohibited Patterns:
- Real vendor tokens or past expired tokens in code comments.
- Synthetic tokens that match real vendor regexes without a placeholder prefix (e.g. raw `AKIA...` or `sk-proj-...` in test fixtures). Use `mock_` or `pytest` monkeypatching instead.

---

## 4. Running the Local Security Tooling

### A. Deterministic Redacted Secret Scanner (`scan_secrets.py`)
Run before any git commit or PR creation:

```bash
# Scan specific modified directories
python3 scripts/scan_secrets.py dashboard/ tests/ docs/

# Strict CI mode (fails with exit code 1 on findings)
python3 scripts/scan_secrets.py --strict
```

### B. Ecosystem Configuration Auditor (`check_ecosystem_secrets.py`)
Validates environment file presence and cross-repo shared secret fingerprints:

```bash
# Standard local audit
python3 scripts/check_ecosystem_secrets.py

# Strict mode for deployment preflight
python3 scripts/check_ecosystem_secrets.py --strict
```

---

## 5. Common False Positives & How to Resolve Them

1. **Documented Environment Variable Names in Text:**
   - *Pattern:* `DOCUSEAL_API_KEY`, `GAS_API_KEY`, `OPENAI_API_KEY`
   - *Handling:* The scanner recognizes uppercase environment variable names as non-secrets automatically.
2. **Fingerprint Hashes:**
   - *Pattern:* `fp:a61e521349` or `sha256:0a9670ab6b...`
   - *Handling:* Prefixed hashes are explicitly recognized as non-secret correlation identifiers.
3. **UUIDs, Mongo ObjectIDs, and CSS Hex Colors:**
   - *Pattern:* `507f1f77bcf86cd799439011`, `#10b981`, `e7d23a10-8b1e-4c7b-912a`
   - *Handling:* Filtered out by entropy and structural context rules.

---

## 6. Truthful Reporting Invariant

When running in an environment without production `.env` files (e.g. fresh developer clone or restricted CI runner):
- `check_ecosystem_secrets.py` reports:
  ```text
  Summary Statistics:
    • Verified Present     : 0
    • Critical Missing     : 0
    • Unverified / Absent  : 9
  Result: ⚪ UNVERIFIED / NOT-PROVEN (Clean checkout without production .env)
  ```
- This guarantees that an unverified environment is never deceptively reported as production-ready.
