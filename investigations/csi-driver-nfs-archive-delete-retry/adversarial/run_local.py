#!/usr/bin/env python3
"""Run controlled filesystem failures against baseline, fix, and two mutations."""
import argparse
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile

BASE = "46878d64d4dee8890998e26f99d07d6361015d60"
FIX = "db7b3934b0c4ef2a9db08edde54ed332f3c598ea"
HERE = Path(__file__).resolve().parent
CONTROLLER = "pkg/nfs/controllerserver.go"
TESTS = "pkg/nfs/controllerserver_test.go"


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--driver", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--go", required=True)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    args.output = args.output.resolve()
    git = lambda *a: subprocess.check_output(["git", "-C", str(args.driver), *a])
    changed = set(git("diff", "--name-only", BASE, FIX).decode().splitlines())
    if changed != {CONTROLLER, TESTS}:
        raise RuntimeError("Recheck the comparison: changes extend beyond the reviewed controller and tests")
    env = dict(os.environ, GOTOOLCHAIN="local", GOFLAGS="-p=4")
    summary = {}
    with tempfile.TemporaryDirectory(prefix="nfs-archive-adversarial-") as tmp:
        src = Path(tmp)
        with tarfile.open(fileobj=io.BytesIO(git("archive", FIX))) as archive:
            archive.extractall(src, filter="data")
        # The upstream TestMain removes a fixed /tmp path. These tests own only
        # t.TempDir paths, so disable that unrelated cleanup in the copied tree.
        test_source = (src / TESTS).read_text()
        assert test_source.count("\tteardown()") == 1
        (src / TESTS).write_text(test_source.replace("\tteardown()", "\t// Adversarial fixtures clean up their own temporary directories."))
        shutil.copyfile(HERE / "archive_adversarial_test.go", src / "pkg/nfs/archive_adversarial_test.go")
        for arm in ["baseline", "fixed", "mutant-stat", "mutant-ignore-errors"]:
            code = git("show", (BASE if arm == "baseline" else FIX) + ":" + CONTROLLER).decode()
            if arm == "mutant-stat":
                assert code.count("os.Lstat(internalVolumePath)") == 1
                code = code.replace("os.Lstat(internalVolumePath)", "os.Stat(internalVolumePath)")
            elif arm == "mutant-ignore-errors":
                needle = "if _, err := os.Lstat(internalVolumePath); err != nil {\n\t\t\t\tif os.IsNotExist(err) {"
                assert code.count(needle) == 1
                code = code.replace(needle, needle.replace("if os.IsNotExist(err)", "if err != nil"))
            for original, replacement in [
                ("os.RemoveAll(archivedInternalVolumePath)", "adversarialRemoveArchive(archivedInternalVolumePath)"),
                ("os.Rename(internalVolumePath, archivedInternalVolumePath)", "adversarialRenameArchive(internalVolumePath, archivedInternalVolumePath)"),
            ]:
                assert code.count(original) == 1
                code = code.replace(original, replacement)
            (src / CONTROLLER).write_text(code)
            result = subprocess.run([args.go, "test", "-json", "./pkg/nfs", "-run", "^TestAdversarial", "-count=1"],
                                    cwd=src, env=env, text=True, capture_output=True)
            (args.output / (arm + ".jsonl")).write_text(result.stdout)
            (args.output / (arm + ".stderr")).write_text(result.stderr)
            tests = {}
            for line in result.stdout.splitlines():
                event = json.loads(line)
                if event.get("Test") and event.get("Action") in ["pass", "fail", "skip"]:
                    tests[event["Test"]] = event["Action"]
            if not tests:
                raise RuntimeError(f"{arm} did not run tests: {result.stderr[-2000:]}")
            summary[arm] = {"exit_code": result.returncode, "tests": tests}
            print(arm, json.dumps(summary[arm]), flush=True)
            if arm in ["baseline", "fixed"]:
                # Keep instrumented Linux binaries for the same filesystem
                # schedule on an already-mounted, dedicated NFS test directory.
                subprocess.run([args.go, "test", "-c", "-o", str(args.output / (arm + ".test")), "./pkg/nfs"],
                               cwd=src, env=dict(env, CGO_ENABLED="0", GOOS="linux", GOARCH="amd64"), check=True)
        (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")


if __name__ == "__main__":
    main()
