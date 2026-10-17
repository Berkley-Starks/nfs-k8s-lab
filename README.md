# NFS + Kubernetes Lab

A public workspace for exploring NFS and Kubernetes storage, reproducing issues,
and developing tests and tools that support upstream contributions.

## Scope

- Minimal reproductions of NFS and Kubernetes storage issues.
- Repeatable lab setups and experiments.
- Regression tests, diagnostic tools, and technical notes.
- Links to related upstream issues and pull requests.

## Layout

Use one folder per issue or investigation:

```text
investigations/
  <project>-<issue-number>-<topic>/
    README.md
    ...reproduction scripts, manifests, and tests
```

For an investigation without an upstream issue yet, use `<project>-<topic>`.
Each investigation's README should link to the upstream issue and pull request
when available.

## Working approach

Keep each experiment self-contained and document its prerequisites, versions,
setup, expected behavior, observed behavior, and cleanup steps. Link findings to
the relevant upstream project so they can inform a focused contribution.

Changes intended for an upstream project belong in a separate clone or fork of
that project, with a branch per issue. Keep those clones alongside this lab
repository rather than nesting Git repositories inside it. This repository holds
the supporting lab work.
