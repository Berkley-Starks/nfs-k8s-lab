#!/usr/bin/env bash
# Bootstrap a fresh Ubuntu node in a dedicated reproduction lab.
set -euo pipefail

role=${1:-}
if [[ $role != server && $role != agent ]]; then
  echo "Usage: NODE_NAME=... NODE_IP=... LAB_ID=... $0 server|agent" >&2
  exit 2
fi
[[ $EUID == 0 ]] || { echo 'Run as root on the new lab node.' >&2; exit 1; }
[[ $(uname -m) == x86_64 ]] || { echo 'This recipe targets Linux amd64.' >&2; exit 1; }
: "${NODE_NAME:?Set a unique node name}"
: "${NODE_IP:?Set the node IPv4 address}"
: "${LAB_ID:?Set the same lab ID on both nodes}"
for value in "$NODE_NAME" "$LAB_ID"; do
  [[ $value =~ ^[a-z0-9]([-a-z0-9]*[a-z0-9])?$ && ${#value} -le 63 ]] || {
    echo 'Node names and lab IDs must be lowercase DNS labels.' >&2; exit 1;
  }
done
[[ $NODE_IP =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || exit 2
if [[ -e /etc/rancher/k3s/config.yaml || -e /var/lib/rancher/k3s || -e /usr/local/bin/k3s ]]; then
  echo 'An existing K3s installation or configuration was found; refusing to replace it.' >&2
  exit 1
fi
if [[ $(swapon --noheadings --show | wc -l) -gt 0 ]]; then
  echo 'Disable swap on this dedicated node before installing Kubernetes.' >&2
  exit 1
fi
if [[ $role == agent ]]; then
  : "${SERVER_IP:?Set the server IPv4 address}"
  : "${JOIN_TOKEN_FILE:?Provide a private file containing the server node-token}"
  [[ $SERVER_IP =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ && -s $JOIN_TOKEN_FILE ]] || exit 2
fi

version='v1.35.9+k3s1'
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl nfs-common
install -d -m 700 /etc/rancher/k3s
umask 077
cat > /etc/rancher/k3s/config.yaml <<EOF
node-name: "$NODE_NAME"
node-ip: "$NODE_IP"
node-label:
  - "nfs-k8s-lab/cluster=$LAB_ID"
EOF
if [[ $role == server ]]; then
  cat >> /etc/rancher/k3s/config.yaml <<'EOF'
write-kubeconfig-mode: "0600"
cluster-cidr: "10.42.0.0/16"
service-cidr: "10.43.0.0/16"
disable:
  - traefik
  - servicelb
  - local-storage
EOF
else
  install -m 600 "$JOIN_TOKEN_FILE" /etc/rancher/k3s/join-token
  cat >> /etc/rancher/k3s/config.yaml <<EOF
server: "https://$SERVER_IP:6443"
token-file: "/etc/rancher/k3s/join-token"
EOF
fi
installer=$(mktemp)
trap 'rm -f "$installer"' EXIT
curl --fail --location --retry 3 \
  "https://raw.githubusercontent.com/k3s-io/k3s/$version/install.sh" -o "$installer"
# The tagged installer checks the release binary against its published checksum.
INSTALL_K3S_VERSION=$version INSTALL_K3S_EXEC=$role sh "$installer"
echo "Installed $version as $role on $NODE_NAME"
