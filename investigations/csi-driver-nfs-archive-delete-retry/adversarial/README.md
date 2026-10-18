# Adversarial review: archive deletion retries

Tested on 2026-10-18 against upstream `46878d64d4de` and the retry fix at
`db7b3934b0c4`. The [test plan](plan.md) records the predictions made before
execution. The fix passes the sequential retry and filesystem-error cases.
A forced overlap between two independent controllers still loses the archive
when `--remove-archived-volume-path=true`. That failure also occurs upstream
and needs a separate fix.

## Results

These tests call the real controller code with fake mount adapters and real
filesystem operations. Each controller has its own locks and deletion cache.

| Test | Upstream | Retry fix |
| --- | --- | --- |
| Five retries through fresh controller instances, replacement off/on | Errors; archive lost when on | Success; original bytes preserved |
| Missing source, existing archive, replacement on | Error; archive lost | Success; archive preserved |
| Source lookup denied by directory permissions | Error; old archive removed | Error; old archive preserved |
| Source parent is a symlink loop | Error; old archive removed | Error; old archive preserved |
| Dangling source symlink | Link archived | Link archived |
| Source symlink points to an external fixture | Target unchanged | Target unchanged |
| Mount check returns I/O error | Error; archive preserved | Error; archive preserved |
| This controller's volume lock is already held | `Aborted`; data unchanged | Same |
| Two independent controllers, replacement off | One `OK`, one `Internal`; archive preserved | Same |
| Two independent controllers, replacement on | One `OK`, one `Internal`; archive lost | Same |

The permission test ran as an unprivileged Linux user. All other cases also
ran against a dedicated NFSv4.1 export on Ubuntu 24.04.3, kernel 6.8.0-124.
The NFS invocation ran as root, so its permission test was explicitly skipped.
Test directories and transferred executables were removed after the run.

Two mutations checked that the tests could reject plausible mistakes:
replacing `Lstat` with `Stat` failed the dangling-link test, and accepting every
lookup error as absence failed both the permission and symlink-loop tests.
These mutations ran only in disposable source copies.

## Independent controller overlap

The fixture adds scheduling barriers around the existing archive-removal and
rename calls in a disposable copy of the controller. It forces this order:

1. A and B reach archive removal. On the retry-fix branch, both have already
   observed that the source exists.
2. A removes any previous archive and renames the source to the archive.
3. B removes the archive just created by A.
4. B's rename fails because the source no longer exists.

The archive's original bytes are then absent. With replacement disabled, B
still returns `Internal`, but A's archive survives. Holding one controller's
in-memory lock rejects another request to that controller; it does not protect
against a different controller instance.

This is a deterministic legal interleaving, reproduced on local storage and
NFS. It uses two controller instances in one test process with fake mount
adapters. It does not measure the frequency of the race or demonstrate an
unmodified deployment failing during leader election. Go's race detector does
not establish correctness of this filesystem protocol.

## Separate-process retry

The CSI RPC harness also ran with two unmodified driver processes per test
pod. The first process archived the volume; the second, whose cache was empty,
handled the retry. Both processes used the same explicitly selected image.
This tests cache loss across processes without simulating a crash or failover.

The upstream image returned `Internal` for both replacement settings and lost
the archive when replacement was enabled. The retry-fix image returned `OK`
and preserved the payload in both cases. Immediate cached retries, ordinary
deletion retries, and deliberate checksum corruption controls behaved as
expected. The runner recorded the first driver's runtime image digest;
the second container's digest was not separately captured in this run.
All test namespaces and synthetic export directories were removed.

Pass `--fresh-controller` to the [NFS runner](../run.py) to select this mode.
The default mode still waits 70 seconds for cache expiry.

## Repeat the filesystem tests

Use Linux, Python 3.12 or later, and the Go version required by the driver:

```sh
python3 investigations/csi-driver-nfs-archive-delete-retry/adversarial/run_local.py \
  --driver ../csi-driver-nfs \
  --go "$HOME/.local/share/nfs-k8s-lab/go1.26.9/bin/go" \
  --output artifacts/archive-delete-retry/adversarial-review
```

The runner pins both source revisions, checks the comparison's file scope,
and builds temporary copies. It records per-test outcomes for the baseline,
fix, and mutations, and produces static Linux test binaries for the NFS check.
The runner itself exits successfully after recording results, including
expected test failures; inspect `summary.json` and the test logs.

For NFS, run each binary with `TMPDIR` set to a newly created directory on a
verified dedicated export and `-test.run=^TestAdversarial -test.count=1 -test.v`.
Each fixture owns a temporary child directory. Check binary hashes before
execution and remove only the owned parent after it is empty. Raw logs and
environment identifiers belong in private artifacts, not this repository.

## Contribution scope

The retry patch establishes that a completed archive deletion can be repeated
without removing its archive, and that lookup failures are not treated as
success. It does not serialize independent controllers. The concurrent-delete
bug will have its own issue, source branch, tests, and PR.
