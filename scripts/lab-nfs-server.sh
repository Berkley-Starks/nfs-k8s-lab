#!/usr/bin/env bash
# Optional Linux NFS reference backend on a dedicated lab node.
set -euo pipefail

[[ $EUID == 0 ]] || { echo 'Run as root on a dedicated lab node.' >&2; exit 1; }
: "${NFS_CLIENTS:?Set the space-separated IPv4 addresses of both lab nodes}"
read -r -a clients <<< "$NFS_CLIENTS"
[[ ${#clients[@]} == 2 && ${clients[0]} != "${clients[1]}" ]] || {
  echo 'Provide two different client IPv4 addresses.' >&2; exit 2;
}
for client in "${clients[@]}"; do
  [[ $client =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || exit 2
done
export_dir=/srv/nfs-k8s-lab
config=/etc/exports.d/nfs-k8s-lab.exports
[[ ! -e $export_dir && ! -e $config ]] || {
  echo 'The reference export already exists; inspect it before making changes.' >&2; exit 1;
}
shopt -s nullglob
for file in /etc/exports /etc/exports.d/*.exports; do
  if [[ -f $file ]] && grep -qE '^[[:space:]]*[^#[:space:]]' "$file"; then
    echo "Existing NFS exports found in $file; refusing to change this server." >&2
    exit 1
  fi
done

export DEBIAN_FRONTEND=noninteractive
if ! dpkg-query -W -f='${Status}' nfs-kernel-server 2>/dev/null | grep -qx 'install ok installed'; then
  apt-get install -y --no-install-recommends nfs-kernel-server
fi
install -d -m 755 /etc/exports.d
install -d -m 777 "$export_dir"
{
  printf '%s' "$export_dir"
  for client in "${clients[@]}"; do
    printf ' %s(rw,sync,root_squash,no_subtree_check,fsid=0)' "$client"
  done
  printf '\n'
} > "$config"
chmod 644 "$config"
exportfs -ra
systemctl enable --now nfs-server
printf 'Reference export ready: %s, mounted by NFSv4 clients as /\n' "$export_dir"
