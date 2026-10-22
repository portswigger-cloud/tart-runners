#!/usr/bin/env bash
# Usage: sudo ./install.sh <user> <config.toml> [seconds to wait for runners, default 600]
# Safe to rerun: running jobs finish before slots pick up a new release or config.
set -euo pipefail

user=${1:?usage: install.sh <user> <config.toml>}
config=${2:?usage: install.sh <user> <config.toml>}
wait=${3:-600}
[[ $EUID -eq 0 ]] || { echo "install.sh must run as root" >&2; exit 1; }

uid=$(id -u "$user")
brew=/opt/homebrew/bin/brew
python=/opt/homebrew/opt/python@3.13/bin/python3.13
root="/Library/Application Support/tart-runners"
src=$(cd "$(dirname "$0")" && pwd)

[[ -x $brew ]] || { echo "Homebrew is required at $brew" >&2; exit 1; }
missing=()
[[ -x $python ]] || missing+=(python@3.13)
[[ -x /opt/homebrew/bin/tart ]] || missing+=(openai/tools/tart)
if (( ${#missing[@]} )); then
  launchctl asuser "$uid" sudo -u "$user" -H env HOMEBREW_NO_INSTALL_UPGRADE=1 "$brew" install --quiet "${missing[@]}"
fi

version=$(cd "$src" && find tart_runners -type f -not -name '*.pyc' | LC_ALL=C sort | xargs shasum -a 256 | shasum -a 256 | cut -c1-12)
release="$root/releases/$version"
if [[ ! -d $release ]]; then
  install -d -m 755 "$root/releases"
  staging=$(mktemp -d "$root/releases/.staging.XXXXXX")
  cp -R "$src/tart_runners" "$staging/"
  find "$staging" -name __pycache__ -prune -exec rm -rf {} +
  chmod -R go-w,a+rX "$staging"
  mv "$staging" "$release"
fi

PYTHONPATH="$release" exec "$python" -m tart_runners install --user "$user" --config "$config" --release "$release" --wait "$wait"
