#!/usr/bin/env bash
set -euo pipefail

# Matches the version pinned in the driver's CI at the recorded baseline.
version=1.26.9
archive_sha256=42d158b4d8f7b61ac0a830567c940a86098fb7aac52e467a5ebec03ef5cc2f8d
install_dir="${XDG_DATA_HOME:-$HOME/.local/share}/nfs-k8s-lab/go${version}"

if [[ $(uname -s) != Linux || $(uname -m) != x86_64 ]]; then
    echo 'This bootstrap supports Linux amd64, including Ubuntu WSL.' >&2
    exit 1
fi
if [[ -x "$install_dir/bin/go" ]]; then
    exec "$install_dir/bin/go" version
fi
if [[ -e "$install_dir" ]]; then
    echo "Incomplete installation exists at $install_dir; inspect it before retrying." >&2
    exit 1
fi

download_dir=$(mktemp -d)
archive="$download_dir/go.tar.gz"
trap 'rm -f -- "$archive"; rmdir -- "$download_dir"' EXIT
curl --fail --location --retry 3 "https://go.dev/dl/go${version}.linux-amd64.tar.gz" -o "$archive"
printf '%s  %s\n' "$archive_sha256" "$archive" | sha256sum --check -
mkdir -p "$install_dir"
tar -xzf "$archive" -C "$install_dir" --strip-components=1
"$install_dir/bin/go" version
