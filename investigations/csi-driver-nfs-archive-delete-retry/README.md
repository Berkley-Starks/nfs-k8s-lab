# Archive deletion retries

The NFS CSI driver returns an error when an archive deletion is retried after
its one-minute deletion cache expires. With
`--remove-archived-volume-path=true`, the retry also removes the saved archive.
The default setting keeps the archive but still returns `Internal`.

Reproduced against upstream commit `46878d64d4de` on 2026-10-18. A fix is on
[`fix/archive-delete-retry`](https://github.com/Berkley-Starks/csi-driver-nfs/tree/fix/archive-delete-retry).
The [lab results](results/2026-10-18.md) show the original build failing and
the fixed build passing the same test. No upstream issue or PR has been opened
for this investigation yet.

The [adversarial review](adversarial/README.md) confirms the retry behavior and
records a separate concurrent-delete bug: two independent controllers can
still remove the archive when replacement is enabled. That race also affects
the original code and is outside this patch's scope.

## Background

[CSI DeleteVolume](https://github.com/container-storage-interface/spec/blob/master/spec.md#deletevolume)
must succeed when the volume is already gone. The driver checks an in-memory
cache before deleting a volume, but that cache expires after one minute and
does not survive a controller restart.

The archive path then tries to rename the missing source directory. When
archive replacement is enabled, it removes the existing archive first.

[Upstream issue #595](https://github.com/kubernetes-csi/csi-driver-nfs/issues/595)
contains earlier reports of repeated archive deletion. It was closed after
changes that included disabling archive replacement by default. This
investigation covers retries that reach the filesystem after the cache is lost.

## Driver change

Check the source with `os.Lstat` before creating archive parents or removing an
existing archive. If the source is absent, record the successful deletion in
the cache and return success. Other lookup errors still return `Internal`.

Regression tests cover flat and nested paths, a fresh controller with an empty
cache, a missing volume, the default archive policy, archive replacement, and
a source lookup error. The Unix-specific lookup error case is skipped on
Windows, which reports a non-directory parent as a missing path.

## Run the unit reproduction

The driver checkout lives alongside this repository. Apply the regression tests
to the original revision, then run from the lab root:

```sh
bash scripts/driver.sh go test ./pkg/nfs -run '^TestDeleteVolumeArchive' -count=1
```

The original revision fails the retry and missing-volume assertions. The fixed
branch passes. The tests use a fake mounter and temporary directories; the lab
test below exercises the actual driver against NFS.

## Run against NFS

Use the dedicated cluster and private environment described in the
[small-lab recipe](../../environments/small-lab/README.md). Required variables
are `KUBECONFIG`, `KUBE_CONTEXT`, `LAB_ID`, `LAB_NODE_A`, `LAB_NODE_B`,
`NFS_SERVER`, and `NFS_SHARE`.

Build a static Linux amd64 client using the driver's vendored dependencies:

```sh
mkdir -p artifacts/archive-delete-retry
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 bash scripts/driver.sh go build \
  -mod=vendor -o ../nfs-k8s-lab/artifacts/archive-delete-retry/repro \
  ../nfs-k8s-lab/investigations/csi-driver-nfs-archive-delete-retry/repro.go
```

Build an image from each source revision with `scripts/driver-image.sh`, use a
different tag for each, and import both into the container runtime on both lab
nodes. Confirm the imported config digest against the image archive before
running either test. The script uses `imagePullPolicy: Never`.

```sh
python3 investigations/csi-driver-nfs-archive-delete-retry/run.py \
  --image nfs-lab/nfsplugin:git-46878d64d4de \
  --binary artifacts/archive-delete-retry/repro --expect bug \
  --output artifacts/archive-delete-retry/baseline.json

python3 investigations/csi-driver-nfs-archive-delete-retry/run.py \
  --image nfs-lab/nfsplugin:git-4ababd56ebde \
  --binary artifacts/archive-delete-retry/repro --expect fixed \
  --output artifacts/archive-delete-retry/fixed.json
```

Each invocation creates two isolated pods, one per node, with archive
replacement disabled and enabled respectively. Each pod has its own driver
process and CSI socket. The driver is called directly, with NFSv4.1 mount
options. Its observer also mounts NFSv4.1, with attribute and lookup caching
disabled so that directory checks see changes made by the driver.

The test creates an archive volume and a normal-delete control, writes a
random 1 MiB payload, deletes the archive volume, and verifies its SHA-256.
An immediate retry must succeed. Deliberate corruption must fail the checksum
check; the payload is then restored. After 70 seconds, both deletion requests
are repeated and the archive's contents are checked again.

`--expect bug` requires the precise original failure, including archive loss
only when replacement is enabled. Setup failures do not count as a reproduction.
`--expect fixed` requires successful retries, an intact archive, and a passing
normal-delete control. Both runs record the actual runtime image ID.

## Cleanup and scope

Successful runs remove their synthetic export directories and namespace. An
unexpected failure retains the namespace for inspection. The runner's `RUN_ID`
identifies its directories: `/export/<RUN_ID>` and
`/export/archived-<RUN_ID>`. Verify those exact paths and namespace ownership
before removing failed-run resources.

The pods are privileged so they can mount NFS. Run this only in the dedicated
lab. This is a direct CSI retry test with synthetic data; testing Kubernetes
provisioner timeout and restart sequences remains a separate integration check.
The result is deterministic at this size, so AWS scale testing is unnecessary
for this fix.
