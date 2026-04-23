---
name: git-sync-deploy
description: Stage, commit, push, and sync code between local machine, GitHub, and Hetzner VPS without conflicts. Handles macOS sandbox DNS restrictions.
---

# Git Sync & Deploy

> Conflict-free code synchronization: Local → GitHub → Hetzner VPS → Docker rebuild.

## When to Use
- User says "deploy", "push", "sync", "update the server"
- After editing scraper files locally
- When VPS code is out of date
- Anytime you need local changes reflected on the production VPS

---

## The Problem

The Antigravity agent runs in a **macOS sandbox** that blocks DNS resolution. This means:
- ❌ `git push` from local terminal fails (`Could not resolve host: github.com`)
- ❌ `scp` from local to VPS fails (same DNS issue)
- ✅ SSH MCP tool CAN connect to VPS (uses its own DNS)
- ✅ VPS CAN reach GitHub (no sandbox)

## The Solution: VPS-Relay Pattern

Since the VPS has full network access AND GitHub credentials, we use it as a relay:

```
Local Files → (SSH fs_write / proc_exec heredoc) → VPS /opt/shamrock-leads/
     ↓                                                      ↓
  git commit (local, optional)                    git add + commit + push
     ↓                                                      ↓
  stays local (can't reach GitHub)               GitHub origin/main updated
                                                            ↓
                                                docker-compose build + up
```

---

## Decision Tree

```
START: "I need code on the VPS"
  │
  ├─ Small change (1-3 files)?
  │   → Use SSH heredoc: `cat > /opt/shamrock-leads/path/file.py << 'EOF' ... EOF`
  │   → Then `docker cp` into running container OR rebuild
  │
  ├─ Medium change (4-15 files)?
  │   → Write files to VPS via SSH heredoc (batch)
  │   → `cd /opt/shamrock-leads && git add -A && git commit -m "msg" && git push origin main`
  │   → `docker-compose build --no-cache && docker-compose up -d`
  │
  ├─ Large change (entire directory / many files)?
  │   → Use the VPS git pull approach:
  │   │   1. Ensure local changes are committed
  │   │   2. Write files to VPS host filesystem
  │   │   3. VPS: git add + commit + push → GitHub
  │   │   4. VPS: docker-compose build + up
  │   │
  │   → OR use tar + base64 transfer (see Transfer Patterns below)
  │
  └─ Full rebuild from scratch?
      → VPS: `cd /opt/shamrock-leads && git fetch origin && git reset --hard origin/main`
      → `docker-compose build --no-cache && docker-compose up -d`
```

---

## Standard Workflows

### Workflow A: Quick File Deploy (1-3 files)

```bash
# 1. Write file to VPS host
cat > /opt/shamrock-leads/scrapers/counties/county_name.py << 'EOF'
# ... file contents ...
EOF

# 2. Copy into running container
docker cp /opt/shamrock-leads/scrapers/counties/county_name.py shamrock-leads:/app/scrapers/counties/

# 3. OR rebuild (preferred for production)
cd /opt/shamrock-leads && docker-compose build --no-cache && docker-compose up -d
```

### Workflow B: Batch Deploy + Git Sync

```bash
# 1. Write all files to VPS (via SSH heredoc or fs_write)
# 2. Commit and push FROM the VPS
cd /opt/shamrock-leads
git add -A
git commit -m "feat: description of changes"
git push origin main

# 3. Rebuild Docker
docker-compose build --no-cache
docker-compose up -d

# 4. Verify
docker logs shamrock-leads --tail 30
```

### Workflow C: Tar Transfer (Large Batches)

When transferring many files, use base64-encoded tar:

```bash
# On local (in agent): read files, base64 encode, write to VPS
# Then on VPS:
echo '<base64_data>' | base64 -d | tar xz -C /opt/shamrock-leads/
```

### Workflow D: Git Reset (Nuclear Option)

When VPS has diverged and you want a clean slate:

```bash
cd /opt/shamrock-leads
git stash  # Save any VPS-only changes
git fetch origin
git reset --hard origin/main
git stash pop  # Restore VPS-only changes (if any)
docker-compose build --no-cache && docker-compose up -d
```

---

## Transfer Patterns

### Pattern: SSH Heredoc (Most Reliable)
```bash
cat > /opt/shamrock-leads/path/to/file.py << 'UNIQUEEOF'
<file contents>
UNIQUEEOF
```

**Rules:**
- Always use a UNIQUE EOF marker (e.g., `LEEEOF`, `COLLEOF`)
- Always SINGLE-QUOTE the EOF marker (`'EOF'`) to prevent shell expansion
- Verify with `wc -l` after writing
- Use `echo "done: $(wc -l < /path/file.py) lines"` for confirmation

### Pattern: Docker CP (Into Running Container)
```bash
docker cp /opt/shamrock-leads/scrapers/counties/file.py shamrock-leads:/app/scrapers/counties/
```
**When:** Quick hotfix without full rebuild.

### Pattern: Docker Rebuild (Production Standard)
```bash
cd /opt/shamrock-leads && docker-compose build --no-cache && docker-compose up -d
```
**When:** Any change that should persist across container restarts.

---

## Conflict Resolution

### Local and VPS Have Diverged
```bash
# On VPS: check divergence
cd /opt/shamrock-leads
git status
git log --oneline HEAD..origin/main  # Commits on remote not on VPS
git log --oneline origin/main..HEAD  # Commits on VPS not on remote

# Resolution: VPS changes take priority (since that's production)
git add -A && git commit -m "chore: sync VPS state"
git push origin main

# Then locally: 
# (Note: can't git pull from local due to sandbox)
# Just track that VPS is the source of truth
```

### Multiple Files Modified on Both Sides
```bash
# Always: VPS is source of truth for production state
# Local is source of truth for new development
# Deploy flow: Local → VPS (via heredoc) → git push (from VPS)
```

---

## VPS Connection Details

| Property | Value |
|----------|-------|
| Host | `178.156.179.237` |
| User | `root` |
| SSH Alias | `shamrock-hetzner` |
| Repo Path | `/opt/shamrock-leads` |
| Container | `shamrock-leads` |
| App Path (in container) | `/app/` |
| Git Remote | `origin` → `github.com/Shamrock2245/shamrock-leads.git` |

---

## Post-Deploy Verification

```bash
# 1. Container health
docker-compose ps

# 2. Logs (first 30 lines of latest run)  
docker logs shamrock-leads --tail 30

# 3. Quick scraper test
docker exec shamrock-leads python main.py --county lee --once

# 4. Full health check
docker exec shamrock-leads python -c "from scrapers.counties import *; print('All imports OK')"
```

---

## Common Gotchas

1. **EOF marker conflicts**: If file content contains the EOF string, use a unique marker
2. **Shell expansion**: Always single-quote the heredoc delimiter (`<< 'EOF'`)
3. **File permissions**: VPS files written as root — Docker COPY handles permissions
4. **`.env` not in git**: The `.env` file lives only on VPS, never committed
5. **Container vs host**: Changes to `/opt/shamrock-leads/` don't affect the running container until rebuild
6. **DNS sandbox**: Never try `git push` or `curl` from the local Antigravity terminal
