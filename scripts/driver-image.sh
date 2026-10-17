#!/usr/bin/env bash
# Build a Linux amd64 driver image with Docker, including from an arm64 host.
set -euo pipefail

root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
driver_dir=${CSI_DRIVER_DIR:-$root/../csi-driver-nfs}
driver_dir=$(cd -- "$driver_dir" && pwd)
: "${NFS_IMAGE:?Set a unique image tag, for example nfs-lab/nfsplugin:git-<commit>}"
command -v docker >/dev/null
[[ -f $driver_dir/go.mod ]] || {
  echo "Driver source not found in $driver_dir" >&2; exit 1;
}

# Building inside the Go image keeps the host toolchain out of the result.
docker run --rm -v "$driver_dir:/src" -w /src \
  -e CGO_ENABLED=0 -e GOOS=linux -e GOARCH=amd64 -e GOTOOLCHAIN=local \
  golang:1.26.9-bookworm \
  go build -mod=vendor -p=4 -o bin/amd64/nfsplugin ./cmd/nfsplugin
docker build --platform linux/amd64 \
  --file "$root/environments/small-lab/Dockerfile.driver" --tag "$NFS_IMAGE" "$driver_dir"
docker image inspect --format '{{.Id}} {{.Os}}/{{.Architecture}}' "$NFS_IMAGE"
