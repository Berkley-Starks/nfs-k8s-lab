#!/usr/bin/env python3
"""Deploy the released NFS CSI driver and exercise a dedicated two-node lab."""

import argparse
import datetime
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys


LABEL = "nfs-k8s-lab/cluster"
DRIVER_VERSION = "v4.13.5"
SMOKE_IMAGE = "busybox:1.37.0"


def required(name):
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Set {name}; see environments/small-lab/.env.example")
    return value


def run(args, data=None):
    result = subprocess.run(args, input=data, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(f"Command failed: {' '.join(args[:5])}\n{result.stderr.strip()}")
    return result.stdout.strip()


def kubectl(*args, obj=None):
    return run(["kubectl", "--kubeconfig", required("KUBECONFIG"),
                "--context", required("KUBE_CONTEXT"), "--request-timeout=30s", *args],
               json.dumps(obj) if obj is not None else None)


def get(*args):
    return json.loads(kubectl("get", *args, "-o", "json"))


def preflight(require_ready=True):
    lab_id = required("LAB_ID")
    nodes = [required("LAB_NODE_A"), required("LAB_NODE_B")]
    if len(set(nodes)) != 2:
        raise RuntimeError("Choose two different lab nodes")
    actual = get("nodes")["items"]
    if {n["metadata"]["name"] for n in actual} != set(nodes):
        raise RuntimeError("This workflow requires a dedicated cluster with exactly the two configured nodes")
    for node in actual:
        labels = node["metadata"].get("labels", {})
        ready = any(c["type"] == "Ready" and c["status"] == "True"
                    for c in node["status"]["conditions"])
        if labels.get(LABEL) != lab_id or labels.get("kubernetes.io/os") != "linux" or (require_ready and not ready):
            raise RuntimeError(f"Node {node['metadata']['name']} is not a Ready Linux member of {lab_id}")
    return nodes


def deploy():
    preflight()
    source = Path(required("CSI_DRIVER_DIR"))
    image = os.environ.get("NFS_IMAGE")
    values = {"controller": {"enableSnapshotter": False}, "externalSnapshotter": {"enabled": False}}
    if image:
        repository, separator, tag = image.rpartition(":")
        if not separator or not repository or not re.fullmatch(r"[a-zA-Z0-9_.-]+", tag):
            raise RuntimeError("NFS_IMAGE must include an explicit repository and tag")
        values["image"] = {"nfs": {"repository": repository, "tag": tag, "pullPolicy": "Never"}}
    chart = source / "charts" / ("latest" if image else DRIVER_VERSION) / "csi-driver-nfs"
    if not (chart / "Chart.yaml").is_file():
        raise RuntimeError(f"Missing released chart: {chart}")
    print(run(["helm", "upgrade", "--install", "csi-driver-nfs", str(chart),
               "--kubeconfig", required("KUBECONFIG"), "--kube-context", required("KUBE_CONTEXT"),
               "--namespace", "kube-system", "--values", "-", "--wait", "--timeout", "5m"],
              json.dumps(values)))
    for kind in ["deployment/csi-nfs-controller", "daemonset/csi-nfs-node"]:
        print(kubectl("rollout", "status", "-n", "kube-system", kind, "--timeout=180s"))


def metadata(name):
    return {"name": name, "labels": {LABEL: required("LAB_ID"),
                                    "app.kubernetes.io/managed-by": "nfs-k8s-lab"}}


def apply(obj):
    kubectl("create", "-f", "-", obj=obj)


def pod(namespace, name, node):
    return {"apiVersion": "v1", "kind": "Pod", "metadata": metadata(name) | {"namespace": namespace},
            "spec": {"nodeName": node, "restartPolicy": "Never", "terminationGracePeriodSeconds": 5,
                     "containers": [{"name": "io", "image": SMOKE_IMAGE,
                                     "command": ["sh", "-c", "sleep 3600"],
                                     "resources": {"requests": {"cpu": "10m", "memory": "16Mi"},
                                                   "limits": {"memory": "64Mi"}},
                                     "volumeMounts": [{"name": "shared", "mountPath": "/data"}]}],
                     "volumes": [{"name": "shared", "persistentVolumeClaim": {"claimName": "shared"}}]}}


def exec_pod(namespace, name, command):
    return kubectl("exec", "-n", namespace, name, "--", "sh", "-ec", command)


def digest(namespace, name, filename):
    return exec_pod(namespace, name, f"sha256sum /data/{filename}").split()[0]


def check_digest(namespace, name, filename, expected):
    actual = digest(namespace, name, filename)
    if actual != expected:
        raise ValueError(f"Checksum mismatch for {filename} on {name}")


def cleanup(namespace):
    if not re.fullmatch(r"nfs-smoke-[a-z0-9-]+", namespace):
        raise RuntimeError("Cleanup accepts only an nfs-smoke-* run name")
    owned = {LABEL: required("LAB_ID"), "app.kubernetes.io/managed-by": "nfs-k8s-lab"}
    existing = set()
    for kind in ["namespace", "storageclass"]:
        output = kubectl("get", kind, namespace, "--ignore-not-found", "-o", "json")
        if not output:
            continue
        obj = json.loads(output)
        if any(obj["metadata"].get("labels", {}).get(k) != v for k, v in owned.items()):
            raise RuntimeError(f"Refusing to delete {kind}/{namespace}: ownership labels differ")
        existing.add(kind)
    pvs = [pv["metadata"]["name"] for pv in get("pv")["items"]
           if pv["spec"].get("claimRef", {}).get("namespace") == namespace]
    if pvs and not existing:
        raise RuntimeError("PV remains but the run's ownership records are missing; inspect it manually")
    if "namespace" in existing:
        kubectl("delete", "namespace", namespace, "--wait=true", "--timeout=180s")
    for pv in pvs:
        kubectl("wait", "--for=delete", f"pv/{pv}", "--timeout=180s")
    if "storageclass" in existing:
        kubectl("delete", "storageclass", namespace)


def smoke():
    nodes = preflight()
    server, share = required("NFS_SERVER"), required("NFS_SHARE")
    namespace = "nfs-smoke-" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M%S-") + secrets.token_hex(2)
    print(f"Run: {namespace}", flush=True)
    apply({"apiVersion": "v1", "kind": "Namespace", "metadata": metadata(namespace)})
    try:
        apply({"apiVersion": "storage.k8s.io/v1", "kind": "StorageClass", "metadata": metadata(namespace),
               "provisioner": "nfs.csi.k8s.io", "parameters": {"server": server, "share": share},
               "reclaimPolicy": "Delete", "volumeBindingMode": "Immediate", "allowVolumeExpansion": True,
               "mountOptions": ["nfsvers=4.1"]})
        apply({"apiVersion": "v1", "kind": "PersistentVolumeClaim",
               "metadata": metadata("shared") | {"namespace": namespace},
               "spec": {"accessModes": ["ReadWriteMany"], "storageClassName": namespace,
                        "resources": {"requests": {"storage": "1Gi"}}}})
        kubectl("wait", "-n", namespace, "pvc/shared", "--for=jsonpath={.status.phase}=Bound", "--timeout=180s")
        for name, node in zip(["writer-a", "writer-b"], nodes):
            apply(pod(namespace, name, node))
        kubectl("wait", "-n", namespace, "pod", "--all", "--for=condition=Ready", "--timeout=180s")
        hashes = {}
        for writer, reader, filename in [("writer-a", "writer-b", "from-a"), ("writer-b", "writer-a", "from-b")]:
            exec_pod(namespace, writer, f"dd if=/dev/urandom of=/data/{filename} bs=1024 count=1024 2>/dev/null; sync")
            hashes[filename] = digest(namespace, writer, filename)
            check_digest(namespace, reader, filename, hashes[filename])
        print("PASS: both nodes read the other node's 1 MiB file", flush=True)
        kubectl("delete", "pod", "-n", namespace, "writer-a", "--wait=true", "--timeout=60s")
        apply(pod(namespace, "writer-a", nodes[0]))
        kubectl("wait", "-n", namespace, "pod/writer-a", "--for=condition=Ready", "--timeout=180s")
        for filename, expected in hashes.items():
            check_digest(namespace, "writer-a", filename, expected)
        print("PASS: files survive pod recreation", flush=True)
        exec_pod(namespace, "writer-b", "cp /data/from-a /data/from-a.saved; printf deliberate-corruption > /data/from-a; sync")
        try:
            check_digest(namespace, "writer-a", "from-a", hashes["from-a"])
        except ValueError:
            print("PASS: checksum check rejects deliberately corrupted data", flush=True)
        else:
            raise RuntimeError("Negative control failed: corrupted data was accepted")
        exec_pod(namespace, "writer-b", "mv /data/from-a.saved /data/from-a; sync")
        check_digest(namespace, "writer-a", "from-a", hashes["from-a"])
        image_ids = sorted({c["imageID"] for p in get("pods", "-n", namespace)["items"]
                            for c in p["status"]["containerStatuses"]})
        print(json.dumps({"run": namespace, "result": "passed", "hashes": hashes,
                          "smoke_image_ids": image_ids}, indent=2), flush=True)
    except Exception:
        print(f"Run retained for inspection. Cleanup: python3 scripts/lab.py cleanup {namespace}", file=sys.stderr)
        raise
    cleanup(namespace)
    print("PASS: namespace, PVC, PV, and StorageClass removed", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["deploy", "smoke", "cleanup"])
    parser.add_argument("run_name", nargs="?")
    args = parser.parse_args()
    if args.command == "cleanup":
        if not args.run_name:
            parser.error("cleanup requires the failed run name")
        preflight(require_ready=False)
        cleanup(args.run_name)
    elif args.command == "deploy":
        deploy()
    else:
        smoke()


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, KeyError) as error:
        print(error, file=sys.stderr)
        sys.exit(1)
