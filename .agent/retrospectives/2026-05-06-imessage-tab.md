## Session Retrospective — 2026-05-06

**Task:** Build the `📱 iMessage Control Center` dashboard tab  
**Commit:** `bb3c9ab` (local, ready to push)  
**Files changed:** `dashboard/index.html`, `dashboard/sl-imessage.js` (new), `dashboard/sl-design-system.css`

---

### What Worked

- **Python shell for complex HTML insertion** — the `replace_file_content` and `multi_replace_file_content` tools
  failed when anchors overlapped or spanned very long blocks. Using `python3 -` heredoc via `run_command`
  was reliable for large string replacement into an existing 2400+ line HTML file.

- **Single-anchor strategy** — inserting the full tab HTML block *before* a unique anchor string
  (`<script src="/sl-notifications.js">`) with a simple `str.replace(..., 1)` call was clean and deterministic.

- **Scoped IIFE module pattern** (`SLiMessage = (() => { ... })()`) matches the existing codebase conventions
  (`SLProspective`, `SLIntake`, `SLCalendar` etc.) — zero friction for tab switching integration.

- **Init guard** (`_state.initialized`) preventing polling from stacking was critical — tab buttons can be
  clicked repeatedly and each click calls `SLiMessage.init()`. Without the guard, 10 tab-clicks = 10 polling
  intervals = API flood.

- **Optimistic toggle UI with rollback** — toggle switches flip immediately, then roll back on API error
  with a toast. Matches what staff expect from a modern dashboard.

- **CSS section 18 pattern** — appending a self-contained `/* 18. iMessage ... */` section to
  `sl-design-system.css` keeps all BB-specific tokens co-located and follows the existing numbered-section
  convention in that file (17 prior sections).

---

### What Didn't Work

- **`multi_replace_file_content` with overlapping chunks** — providing 3 chunks where chunk 2 and 3 both
  touched the same line range caused chunk 3 to silently fail with "replacement overlaps with previous
  replacement". Should always ensure chunks are non-overlapping before submitting.

- **`replace_file_content` for large tail blocks** — when the target file had messy state (stray comments
  after `</body>`), the exact `TargetContent` string no longer matched because whitespace and newlines
  differed. The `python3` shell approach bypassed this entirely.

- **Initial 3-chunk injection attempt** — tried to do tab button, script tag, and full tab HTML in one
  `multi_replace_file_content` call. The tab HTML (175 lines) and script tag were too close together in
  the file and the tool refused overlapping chunks.

---

### Lessons Learned

1. **For large HTML insertions (>50 lines) into existing files, prefer `python3` shell heredoc over
   `replace_file_content`.** The tool's exact-match requirement breaks easily with large targets.

2. **Always do a sanity-check `run_command` (grep or python assert) after any large file manipulation**
   before claiming success. 7-point checklist caught the stray comment issue immediately.

3. **`multi_replace_file_content` chunks must be completely non-overlapping.** If any two chunks touch
   the same line numbers, the later ones will fail silently with an overlap error.

4. **When injecting HTML before `</body>`, the safest anchor is the last `<script>` tag before it**,
   not `</body>` itself, because extra content may have accumulated after `</body>`.

5. **Init guards are mandatory for any SL module called from a tab button.** Tab buttons are clicked
   multiple times in normal usage.

---

### Patterns Discovered (Repeatable)

| Pattern | Situation | Recommended Tool |
|---------|-----------|-----------------|
| Inserting 50+ lines into large HTML | Tab panel injection | `python3` shell heredoc |
| Adding CSS component block | New dashboard module | Append numbered section to `sl-design-system.css` |
| Tab module JS structure | New SL tab | IIFE + `_state` object + `init()` guard + public API object |
| Post-edit verification | Any HTML/JS change | `run_command` grep-based sanity checks |

---

### Infrastructure Changes

- **New file:** `dashboard/sl-imessage.js` — `SLiMessage` module, 390 LOC
- **Modified:** `dashboard/index.html` — tab button + 175-line tab panel inserted
- **Modified:** `dashboard/sl-design-system.css` — CSS section 18 appended (190 lines)
- **Pending Hetzner deploy:** `git pull && docker compose up -d` on VPS to serve new files

### API Endpoints Connected

| Endpoint | Purpose | Module |
|----------|---------|--------|
| `GET /api/bb-health/status` | Connection health + server info | `bb_health_bp` |
| `GET /api/imessage/inbox` | Inbound message list | `imessage_auto_bp` |
| `POST /api/imessage/send` | Send iMessage | `imessage_auto_bp` |
| `POST /api/imessage/mark-read` | Mark conversation read | `imessage_auto_bp` |
| `GET /api/imessage/findmy` | FindMy device list | `imessage_auto_bp` |
| `GET /api/automation/config` | Automation flags | `automation_bp` |
| `POST /api/automation/toggle/<key>` | Flip toggle | `automation_bp` |
| `GET/POST /api/imessage/auto-reply/config` | Auto-reply AI settings | `imessage_auto_bp` |

---

### Knowledge Gaps

- `GET /api/imessage/inbox` response shape is not yet confirmed — the JS defensively handles
  both `data.messages`, `data.chats`, and raw array forms. Should verify against actual
  `imessage_auto_bp` response once the endpoint is live on Hetzner.

- `GET /api/bb-health/status` field names (`connected` vs `server_online`, `version` vs
  `server_version`) need to be confirmed against `bb_health_bp`. The JS handles multiple
  field name variants as a safety net.

---

### Continuous Improvement Checklist

- [x] Git changes committed with descriptive message (`bb3c9ab`)
- [x] New failure mode documented (large HTML injection → use python3 heredoc)
- [x] New pattern documented (IIFE tab module structure)
- [ ] `git push` to GitHub — blocked by macOS sandbox DNS (user must run manually)
- [ ] Hetzner deploy — user must `git pull && docker compose up -d dashboard` on VPS
- [ ] Verify `GET /api/bb-health/status` field schema matches frontend expectations
- [ ] Confirm inbox endpoint response shape (`/api/imessage/inbox`)
