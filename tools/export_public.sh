#!/usr/bin/env bash
# Copy the public part of this project into a clone of github.com/davidsaid/tenebrio-photoperiod-rig.
#
# The wiki (claude-wiki/), CLAUDE.md, .secrets/ and this project's git history stay private.
# Usage: tools/export_public.sh [path to the public clone]
set -euo pipefail
cd "$(dirname "$0")/.."
dest="${1:-$HOME/src/claude-repos/tenebrio-photoperiod-rig}"
mkdir -p "$dest"
rsync -a --delete --exclude '__pycache__' --exclude '.pytest_cache' lights/ "$dest/lights/"
rsync -a --delete docs/{bill-of-materials.md,setup-guide.md,wiring.md,using-the-website.md,system-wiring.pdf,system-wiring.png,sensor-wiring.pdf,sensor-wiring.png} "$dest/docs/"
rsync -a --delete tools/{system_wiring.py,sensor_wiring.py,export_public.sh} "$dest/tools/"
cp README.md LICENSE .gitignore "$dest/"
echo "exported to $dest"
