#!/usr/bin/env python3
"""Compare deletion retries in isolated pods on a dedicated two-node lab."""

import argparse
import concurrent.futures
import importlib.util
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("lab", ROOT / "scripts/lab.py")
lab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lab)


def command(*args):
    return ["kubectl", "--kubeconfig", lab.required("KUBECONFIG"),
            "--context", lab.required("KUBE_CONTEXT"), *args]


def pod(namespace, name, image, node, remove_archive):
    return {
        "apiVersion": "v1", "kind": "Pod",
        "metadata": {**lab.metadata(name), "namespace": namespace},
        "spec": {
            "nodeName": node, "restartPolicy": "Never",
            "terminationGracePeriodSeconds": 10,
            "automountServiceAccountToken": False,
            "containers": [
                {"name": "driver", "image": image, "imagePullPolicy": "Never",
                 "command": ["/nfsplugin"],
                 "args": ["--endpoint=unix:///csi/csi.sock", "--nodeid=archive-repro",
                          "--working-mount-dir=/work", "--mount-permissions=0",
                          f"--remove-archived-volume-path={str(remove_archive).lower()}", "-v=2"],
                 "securityContext": {"privileged": True},
                 "resources": {"requests": {"cpu": "10m", "memory": "64Mi"}, "limits": {"memory": "256Mi"}},
                 "volumeMounts": [{"name": "socket", "mountPath": "/csi"},
                                  {"name": "work", "mountPath": "/work"}]},
                {"name": "runner", "image": image, "imagePullPolicy": "Never",
                 "command": ["sh", "-c", 'mkdir -p /export && mount -t nfs -o nfsvers=4.1,actimeo=0,lookupcache=none "$NFS_SERVER:$NFS_SHARE" /export && exec sleep 1800'],
                 "securityContext": {"privileged": True},
                 "readinessProbe": {"exec": {"command": ["mountpoint", "-q", "/export"]}, "periodSeconds": 2},
                 "resources": {"requests": {"cpu": "10m", "memory": "32Mi"}, "limits": {"memory": "128Mi"}},
                 "env": [{"name": key, "value": lab.required(key)} for key in ["NFS_SERVER", "NFS_SHARE"]]
                        + [{"name": "RUN_ID", "value": "nfs-archive-" + secrets.token_hex(6)}],
                 "volumeMounts": [{"name": "socket", "mountPath": "/csi"}]},
            ],
            "volumes": [{"name": "socket", "emptyDir": {}}, {"name": "work", "emptyDir": {}}],
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, help="Explicit preloaded driver image tag")
    parser.add_argument("--binary", required=True, type=Path, help="Linux amd64 repro binary")
    parser.add_argument("--expect", choices=["bug", "fixed"], required=True)
    parser.add_argument("--output", required=True, type=Path, help="Private JSON evidence path")
    args = parser.parse_args()
    nodes = lab.preflight()
    if args.output.exists():
        raise RuntimeError("Choose a new output path; existing evidence will not be overwritten")
    binary = args.binary.read_bytes()
    namespace = "nfs-archive-" + secrets.token_hex(6)
    lab.apply({"apiVersion": "v1", "kind": "Namespace", "metadata": lab.metadata(namespace)})
    outcomes = {}

    def exercise(name):
        lab.kubectl("wait", "-n", namespace, "--for=condition=Ready", "pod/" + name, "--timeout=180s")
        subprocess.run(command("exec", "-i", "-n", namespace, name, "-c", "runner", "--",
                               "sh", "-c", "cat > /tmp/repro && chmod 700 /tmp/repro"),
                       input=binary, check=True, capture_output=True, timeout=60)
        result = subprocess.run(command("exec", "-n", namespace, name, "-c", "runner", "--", "/tmp/repro"),
                                capture_output=True, text=True, timeout=300)
        # stdout contains only structured observations; transport/setup failures are not a reproduction.
        if not result.stdout.strip():
            raise RuntimeError(f"{name}: reproduction setup failed: {result.stderr.strip()}")
        observation = json.loads(result.stdout)
        observation["exit_code"] = result.returncode
        observation["driver_image"] = next(c["imageID"] for c in lab.get("pod", name, "-n", namespace)
                                             ["status"]["containerStatuses"] if c["name"] == "driver")
        return observation

    try:
        for index, remove_archive in enumerate([False, True]):
            name = "replace-" + str(remove_archive).lower()
            lab.apply(pod(namespace, name, args.image, nodes[index], remove_archive))
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            jobs = {name: executor.submit(exercise, name) for name in ["replace-false", "replace-true"]}
            for name, job in jobs.items():
                outcomes[name] = job.result()
                print(name, json.dumps(outcomes[name]), flush=True)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps({"image": args.image, "namespace": namespace,
                                          "expected": args.expect, "results": outcomes}, indent=2) + "\n")
        os.chmod(args.output, 0o600)
        for name, result in outcomes.items():
            assert result["cached_retry_passed"] and result["checksum_negative_control_passed"]
            assert result["normal_delete_retry_code"] == "OK" and result["normal_delete_absent"]
            assert result["source_absent"] and result["cleanup_passed"]
            if args.expect == "bug":
                assert result["exit_code"] != 0 and result["archive_retry_code"] == "Internal"
                assert result["archive_preserved"] == (name == "replace-false")
            else:
                assert result["exit_code"] == 0 and result["archive_retry_code"] == "OK"
                assert result["archive_preserved"]
    except Exception:
        print(f"Investigation resources retained in {namespace}; inspect before cleanup.", file=sys.stderr)
        raise
    else:
        actual = lab.get("namespace", namespace)
        if actual["metadata"].get("labels", {}).get(lab.LABEL) != lab.required("LAB_ID"):
            raise RuntimeError("Namespace ownership changed; refusing cleanup")
        lab.kubectl("delete", "namespace", namespace, "--wait=true", "--timeout=90s")
        print("Expected result confirmed; test directories and namespace removed.")


if __name__ == "__main__":
    main()
