# Investigations

Start with the [NFS CSI driver baseline](csi-driver-nfs-baseline/README.md) for
the prepared checkout, build tooling, and initial test results.

The first fix is [archive deletion retries](csi-driver-nfs-archive-delete-retry/README.md):
the original driver fails retries after cache expiry and can remove an archived
volume; the fixed build passes the same NFS reproduction.

Create a folder named `<project>-<issue-number>-<topic>` for each issue, or
`<project>-<topic>` when no upstream issue exists yet.

Document the following in each investigation's README:

- The question or bug and links to the upstream issue and pull request.
- Environment, versions, and prerequisites.
- Steps to reproduce, including any scripts or manifests.
- Expected and observed behavior.
- Findings and regression checks.
- Cleanup steps.

Keep upstream source changes in that project's separate clone or fork, using a
branch per issue.
