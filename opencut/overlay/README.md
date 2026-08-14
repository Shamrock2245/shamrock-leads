# Shamrock OpenCut overlay

Copied onto `opencut-classic` after `git clone` in `Dockerfile`.

Keeps bundled SFX, extra GPU effects, and the media catalog when the VPS rebuilds
from upstream classic. Edit the source of truth in
`../opencut-classic`, then recopy here before `docker compose --profile edit build`.
