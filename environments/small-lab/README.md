# Small NFS CSI lab

Use two dedicated Linux VMs and an NFS export to establish a working baseline.
The control-plane node also runs test pods, so the smoke test can check shared
access across two hosts without a third Kubernetes VM.

| Component | Baseline |
| --- | --- |
| Clients | Two Ubuntu 24.04 amd64 VMs, 40 GiB root disks |
| Kubernetes | K3s `v1.35.9+k3s1` |
| NFS CSI driver | Released chart and image `v4.13.5` |
| NFS | Dedicated writable export, NFSv4.1 |
| Smoke workload | BusyBox `1.37.0`, one RWX PVC, two pods |

Allow at least two CPUs and 2 GiB RAM for the server. Leave additional memory for
the CSI controller and test pods. Both nodes need access to the NFS metadata and
data endpoints. Between the nodes, allow TCP 6443, TCP 10250, and UDP 8472 for
the API, kubelet, and Flannel. Keep these ports within the lab network.
Check that the pod and service ranges, `10.42.0.0/16` and `10.43.0.0/16`, do not
overlap existing routes before installing.

## Claim and identify the machines

Use the lab's provisioning workflow to create a separate environment. Keep a
private record of its owner, claim ID, VM names, addresses, software build,
expiration, and renewal/release commands. Confirm each machine's hostname and
claim before configuring it; addresses may be reused after a claim expires.

This repository contains the portable setup. Provider-specific provisioning,
live inventories, credentials, kubeconfigs, and raw logs stay outside it.
The NFS server should use a dedicated export that permits the clients to create
and remove directories. A PVC's requested capacity does not establish an NFS
server quota.

## Install Kubernetes

Copy `scripts/lab-node.sh` to each new client. Run it as root with a distinct
node name and the same `LAB_ID` on both nodes. For the first node:

```sh
NODE_NAME=small-control NODE_IP="$CONTROL_IP" LAB_ID=nfs-lab \
  bash lab-node.sh server
```

Transfer `/var/lib/rancher/k3s/server/node-token` privately from the server to a
root-readable file on the second node. Then install the agent:

```sh
NODE_NAME=small-worker NODE_IP="$WORKER_IP" LAB_ID=nfs-lab \
  SERVER_IP="$CONTROL_IP" JOIN_TOKEN_FILE=/root/nfs-lab-join-token \
  bash lab-node.sh agent
rm /root/nfs-lab-join-token
```

The helper installs NFS client utilities and a pinned K3s release. It disables
Traefik, ServiceLB, and local-path storage. It refuses to replace an existing K3s
installation and requires swap to be disabled. If installation fails after the
configuration is written, inspect that node before retrying; the helper will
not silently overwrite the partial setup.

Copy `/etc/rancher/k3s/k3s.yaml` from the server to a private kubeconfig on the
management host. Change its API address from loopback to the server address and
give the context a distinct name. Keep the file mode at `0600`. Use a separate
`KUBECONFIG` instead of merging it into an existing cluster configuration.

## Install the driver and check storage

The management host needs Python 3.9+, kubectl, Helm, and the public driver
checkout. Copy `.env.example` to a private location and fill in the values:

```sh
source "$HOME/.config/nfs-k8s-lab/small-lab.env"
kubectl --context "$KUBE_CONTEXT" get nodes
python3 scripts/lab.py deploy
python3 scripts/lab.py smoke
```

`deploy` uses the `charts/v4.13.5/csi-driver-nfs` chart in `CSI_DRIVER_DIR`.
Snapshot support is disabled for this initial volume test. The helper checks
that the selected cluster has exactly the two configured Linux nodes, both
Ready and labeled with the expected lab ID, before making changes.

Each smoke run creates its own namespace, StorageClass, and PVC. It checks:

1. Dynamic provisioning binds a ReadWriteMany volume.
2. Pods on different nodes write 1 MiB files and verify each other's SHA-256 hashes.
3. Files retain their contents after a pod is deleted and recreated.
4. The checksum check rejects deliberate corruption, then passes after restoration.
5. Deleting the test namespace removes its PVC and PV; the StorageClass is removed too.

The script prints the test image digest and file hashes. Capture output in the
private claim directory. On failure it leaves the run for inspection and prints
a cleanup command. Cleanup requires the expected ownership labels and removes
only that run. PV deletion confirms that the driver completed `DeleteVolume`;
check the export separately when investigating server-side directory cleanup.

## Capture a result

Record the lab repository commit, driver commit or release, image digests,
Kubernetes and kernel versions, NFS mount options, workload parameters, and
pass/fail assertions. Keep raw outputs private and publish a short result with
hostnames, endpoints, account details, and identifiers removed.

The initial cluster check uses the released driver. The development binary built
with `scripts/driver.sh build` is a separate artifact; it is not deployed by this
recipe. For an issue, build a container from the selected source commit, record
its digest, and run the same test on the baseline and proposed fix.

## Move a reproduction to AWS

First make the issue-specific reproduction reliable here. Run an unaffected
control case and check that the harness can detect the expected failure. Compare
the baseline and fix with the same kernel, mount options, data, and workload.

For AWS, reserve dedicated resources and use the same assertions. Increase one
variable at a time: client count, concurrency, data size, or failure duration.
Record the topology and any differences from the small lab. A two-node K3s test
does not establish EKS behavior or performance at scale. Keep fault injection
within the claimed resources, and record its restoration steps before starting.

## Keep or release the lab

Successful smoke runs remove their Kubernetes test resources. They leave the
driver and cluster installed for the next investigation. Renew the infrastructure
claim before it expires if work is continuing. When finished, release only the
recorded claim through its provisioning system and verify that it is gone.

References: [K3s requirements](https://docs.k3s.io/installation/requirements),
[K3s configuration](https://docs.k3s.io/installation/configuration),
[K3s release](https://github.com/k3s-io/k3s/releases/tag/v1.35.9%2Bk3s1), and
[NFS CSI release](https://github.com/kubernetes-csi/csi-driver-nfs/releases/tag/v4.13.5).
