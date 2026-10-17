#!/usr/bin/env bash
set -euo pipefail

lab_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
driver_dir="${CSI_DRIVER_DIR:-$(dirname -- "$lab_dir")/csi-driver-nfs}"
go_dir="${XDG_DATA_HOME:-$HOME/.local/share}/nfs-k8s-lab/go1.26.9"
if [[ ! -x "$go_dir/bin/go" ]]; then
    echo "Run bash $lab_dir/scripts/setup-go.sh first." >&2
    exit 1
fi
if [[ ! -f "$driver_dir/go.mod" ]]; then
    echo "Driver checkout missing at $driver_dir; clone it alongside the lab or set CSI_DRIVER_DIR." >&2
    exit 1
fi
export PATH="$go_dir/bin:$PATH"
export GOTOOLCHAIN=local
export GOFLAGS="${GOFLAGS:--p=4}"
cd -- "$driver_dir"

action="${1:-unit}"
if (( $# > 0 )); then shift; fi
case "$action" in
    unit) exec go test -covermode=count -coverprofile=profile.cov ./pkg/... -v "$@" ;;
    race) exec go test -race -covermode=atomic -coverprofile=profile.cov ./pkg/... "$@" ;;
    build) exec env CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -mod=vendor -o bin/amd64/nfsplugin ./cmd/nfsplugin "$@" ;;
    go) exec go "$@" ;;
    *) echo 'Usage: driver.sh {unit|race|build|go [arguments...]}' >&2; exit 2 ;;
esac
