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

as_user_brew() {
  launchctl asuser "$uid" sudo -u "$user" -H env HOMEBREW_NO_INSTALL_UPGRADE=1 "$brew" "$@"
}

tap() {
  plutil -extract source.tap raw "/opt/homebrew/opt/$1/INSTALL_RECEIPT.json" 2>/dev/null || true
}

[[ -x $brew ]] || { echo "Homebrew is required at $brew" >&2; exit 1; }

# Homebrew 7 refuses formulas from untrusted taps, and naming tart only trusts tart, not its softnet dependency.
# Trust both before removing anything, so a refusal can't leave the host without tart.
as_user_brew tap openai/tools
as_user_brew trust --quiet --formula openai/tools/tart openai/tools/softnet

# Tart moved from Cirrus Labs to OpenAI, and the old tap's formulas no longer get updates.
# Running VMs keep their binary, and slots retry if they call tart mid-swap.
for formula in tart softnet; do
  old_tap=$(tap "$formula")
  if [[ -n $old_tap && $old_tap != openai/tools ]]; then
    as_user_brew uninstall --quiet --ignore-dependencies "$formula"
  fi
done

missing=()
[[ -x $python ]] || missing+=(python@3.13)
[[ -x /opt/homebrew/bin/tart ]] || missing+=(openai/tools/tart)
if (( ${#missing[@]} )); then
  as_user_brew install --quiet "${missing[@]}"
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
