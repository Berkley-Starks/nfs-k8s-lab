# Adversarial test plan

Target: `db7b3934b0c4ef2a9db08edde54ed332f3c598ea`. Baseline:
`46878d64d4dee8890998e26f99d07d6361015d60`.

Claim under test: after a completed archive deletion, another deletion with
the same volume ID succeeds and leaves the archived contents unchanged, even
when the controller has no deletion cache. A failed source lookup must not be
mistaken for a missing source. Existing archive replacement must still work.

Predictions before execution:

| Case | Baseline prediction | Fix prediction |
| --- | --- | --- |
| Fresh-controller retry, replacement off/on | Error; archive lost when on | Success; archive preserved |
| Permission denied on source parent | Error; old archive may be removed | Error; old archive preserved |
| Source parent is a symlink loop | Error; old archive may be removed | Error; old archive preserved |
| Source is a dangling symlink | Archive the link | Archive the link |
| Source symlink points outside volume | Rename link; target unchanged | Rename link; target unchanged |
| Missing source and existing archive | Error; archive lost when replacement on | Success; archive preserved |
| Mount check fails | Error; archive preserved | Error; archive preserved |
| Another request holds this controller's volume lock | `Aborted`; contents unchanged | Same |
| Two controllers pass the source check before either archives, replacement on | Possible archive loss | Possible archive loss: guard is not atomic |
| Same overlap with replacement off | One rename error; archive preserved | Same |

The overlap test will use explicit scheduling barriers around the existing
archive removal and rename calls. It will record a legal interleaving, not
estimate its frequency in production. The same scheduling will run against
both revisions. A single-controller locked-request test is the control.

Sensitivity checks will replace `Lstat` with `Stat`, then separately treat
every lookup error as absence. The symlink and permission tests must reject
these mutations. Neither mutation belongs on the contribution branch.

Real NFS checks will exercise a fresh controller process and filesystem edge
cases in isolated test resources. Tests must use synthetic data, confirm image
identity, and remove only their own directories and pods. The normal cluster
driver and server networking are outside the fault scope.
