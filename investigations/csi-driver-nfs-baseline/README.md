# NFS CSI driver baseline

Prepare a repeatable development environment before selecting the first issue.

- Upstream: https://github.com/kubernetes-csi/csi-driver-nfs
- Fork: https://github.com/Berkley-Starks/csi-driver-nfs
- Baseline commit: `46878d64d4dee8890998e26f99d07d6361015d60`
- Issue and pull request: not selected yet.
- Development guide: https://github.com/kubernetes-csi/csi-driver-nfs/blob/master/docs/csi-dev.md
- Contribution guide: https://github.com/kubernetes-csi/csi-driver-nfs/blob/master/CONTRIBUTING.md

## Workspace

Open `nfs-k8s.code-workspace` from the lab root in VS Code. It includes only the
public lab and the sibling `csi-driver-nfs` checkout. The driver uses `origin` for
the personal fork and `upstream` for the Kubernetes project.

## Build and unit tests

The helpers target Linux amd64. This workstation uses Ubuntu under WSL 2 with
`make`, GCC, Git, curl, tar, and sha256sum. Go 1.26.9 matches the version pinned in
upstream's Trivy workflow at this baseline; `go.mod` requires at least Go 1.26.0.

From PowerShell in the lab root:

```powershell
wsl -d Ubuntu --cd "$PWD" -- bash scripts/setup-go.sh
wsl -d Ubuntu --cd "$PWD" -- bash scripts/driver.sh unit
wsl -d Ubuntu --cd "$PWD" -- bash scripts/driver.sh build
```

The equivalent VS Code tasks are included in the workspace. In Linux, run the
same Bash scripts directly from the lab root. The Go installer checks the
archive's official SHA-256 checksum and installs under
`~/.local/share/nfs-k8s-lab/go1.26.9`, without changing the global shell PATH.
Set `CSI_DRIVER_DIR` if the driver checkout lives elsewhere.

For a targeted test or race checks:

```sh
bash scripts/driver.sh go test ./pkg/nfs -run '<test-name>' -count=1
bash scripts/driver.sh race
```

## Start an issue

In the driver checkout, fetch upstream and create a branch for the selected issue:

```sh
git fetch upstream
git switch -c fix/<issue-number>-<topic> upstream/master
```

Create a separate investigation folder for that issue, record its reproduction,
and link its upstream PR. Keep this folder for environment and baseline notes.
The upstream contribution guide also links the Kubernetes CLA required for PRs.

## Validation status

Validated on 2026-10-17 using Go 1.26.9 on Ubuntu 26.04 under WSL 2:

- The upstream `./pkg/...` unit tests passed, with 81.2% statement coverage in
  `pkg/nfs`. The helper uses the same Go test command as `make unit-test` while
  avoiding Makefile Git metadata scans across the Windows filesystem.
- The checkout uses the committed LF line endings for Linux tools.
- The Linux amd64 development build passed using vendored dependencies and
  produced `bin/amd64/nfsplugin`. It omits upstream release-version linker metadata.

The [small-lab recipe](../../environments/small-lab/README.md) provides the next
step: a dedicated two-node Kubernetes cluster with a released driver and an NFS
export. Follow it with an issue-specific reproduction, then AWS scale testing
using the same workload and assertions. Actual host inventories, account details,
and credentials are maintained outside this public repository.

The [2026-10-17 small-lab result](results/2026-10-17-small-lab.md) records two Ready
nodes, a successful released-driver rollout, and working ownership checks.
Lab access was subsequently lost. Provisioning then failed to resume after an
orchestration restart. Volume lifecycle tests and source-image deployment await
lab recovery.

## Cleanup

The driver's generated binary is under `bin/amd64/nfsplugin` and unit-test
coverage is in `profile.cov`; both are ignored by the upstream repository.
Go's build cache can be cleared with `bash scripts/driver.sh go clean -cache`.
The Go toolchain is isolated in the user data directory described above.
