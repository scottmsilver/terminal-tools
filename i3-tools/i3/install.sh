#!/usr/bin/env bash
# Install this repo's i3 config onto the current machine by copying.
# Existing ~/.config/i3/config is backed up to config.bak.<timestamp>.
#
# We copy rather than symlink because `set_dpi.sh` does in-place `sed`
# on the live config to retune font/gap/border sizes; a symlink would
# route those DPI edits back into the tracked repo file. Re-run this
# script after pulling to pick up upstream changes.
#
# Usage: ./install.sh

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_DIR="$HOME/.config/i3"
TARGET_CONFIG="$TARGET_DIR/config"
CONF_D="$TARGET_DIR/conf.d"

mkdir -p "$TARGET_DIR" "$CONF_D"

# Back up any existing config — file OR symlink — with a timestamp. `-e`
# follows symlinks, so test both.
backup=""
if [[ -e "$TARGET_CONFIG" || -L "$TARGET_CONFIG" ]]; then
  backup="$TARGET_CONFIG.bak.$(date +%Y%m%d-%H%M%S)"
  mv "$TARGET_CONFIG" "$backup"
  echo "Backed up existing config → $backup"
fi

cp "$REPO_DIR/config" "$TARGET_CONFIG"
echo "Copied $REPO_DIR/config → $TARGET_CONFIG"

# Seed a local override file from the example if none exists.
if ! compgen -G "$CONF_D/*.conf" > /dev/null; then
  cp "$REPO_DIR/conf.d/local.conf.example" "$CONF_D/local.conf"
  echo "Seeded $CONF_D/local.conf (edit to taste)"
else
  echo "$CONF_D already has overrides; leaving them alone."
fi

# Validate. If it fails, roll back so the user's previous config is live.
if command -v i3 >/dev/null; then
  if i3 -C -c "$TARGET_CONFIG" >/dev/null 2>&1; then
    echo "Config validates."
    # Check for companion paths BEFORE reloading i3. Reloading with a
    # missing polybar launcher or script would silently break a running
    # session; better to warn and let the user finish INSTALL.md first.
    missing=()
    [[ -e "$HOME/.config/polybar/launch.sh" ]] || missing+=("~/.config/polybar/launch.sh")
    [[ -e "$HOME/scripts/set_dpi.sh" ]] || missing+=("~/scripts/set_dpi.sh")
    [[ -e "$HOME/scripts/fix-workspaces.py" ]] || missing+=("~/scripts/fix-workspaces.py")
    if (( ${#missing[@]} )); then
      echo
      echo "NOTE: the config references paths that aren't installed yet:"
      printf '  - %s\n' "${missing[@]}"
      echo "See INSTALL.md for the full symlink setup."
      echo "Skipping i3-msg reload to avoid disrupting a running session."
    elif pgrep -x i3 >/dev/null; then
      i3-msg reload >/dev/null && echo "i3 reloaded."
    fi
  else
    echo "WARNING: config failed validation."
    rm -f "$TARGET_CONFIG"
    if [[ -n "$backup" ]]; then
      mv "$backup" "$TARGET_CONFIG"
      echo "Restored previous config from $backup."
    fi
    echo "Run 'i3 -C -c $REPO_DIR/config' for details."
    exit 1
  fi
fi
