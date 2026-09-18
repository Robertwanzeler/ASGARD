#!/usr/bin/env bash
# Install the one-time, root-owned cgroup delegation bootstrap.
set -euo pipefail

readonly PROJECT_ROOT=/home/robert/orange_nuclear
readonly SOURCE_SETUP="${PROJECT_ROOT}/scripts/setup_greenran_cgroup_delegation.sh"
readonly SOURCE_UNIT="${PROJECT_ROOT}/systemd/greenran-cgroup-delegation.service"
readonly SOURCE_DISPATCHER_UNIT="${PROJECT_ROOT}/systemd/greenran-campaign-dispatcher.service"
readonly SOURCE_DISPATCHER="${PROJECT_ROOT}/scripts/greenran_campaign_dispatcher.py"
readonly INSTALL_DIR=/usr/local/lib/greenran
readonly INSTALLED_SETUP="${INSTALL_DIR}/setup_greenran_cgroup_delegation.sh"
readonly UNIT_DEST=/etc/systemd/system/greenran-cgroup-delegation.service
readonly USER_SYSTEMD_DIR=/home/robert/.config/systemd/user
readonly USER_DISPATCHER_DEST="${USER_SYSTEMD_DIR}/greenran-campaign-dispatcher.service"

fail() {
  printf 'ERRO: %s\n' "$*" >&2
  exit 1
}

[[ "${EUID}" -eq 0 ]] || fail "execute uma única vez com sudo: sudo bash scripts/install_greenran_cgroup_delegation.sh"
[[ -f "${SOURCE_SETUP}" ]] || fail "bootstrap ausente: ${SOURCE_SETUP}"
[[ -f "${SOURCE_UNIT}" ]] || fail "unidade systemd ausente: ${SOURCE_UNIT}"
[[ -f "${SOURCE_DISPATCHER_UNIT}" ]] || fail "unidade dispatcher ausente: ${SOURCE_DISPATCHER_UNIT}"
[[ -f "${SOURCE_DISPATCHER}" ]] || fail "dispatcher ausente: ${SOURCE_DISPATCHER}"

install -d -o root -g root -m 0755 "${INSTALL_DIR}"
install -o root -g root -m 0755 "${SOURCE_SETUP}" "${INSTALLED_SETUP}"
install -o root -g root -m 0644 "${SOURCE_UNIT}" "${UNIT_DEST}"
install -d -o robert -g robert -m 0755 "${USER_SYSTEMD_DIR}"
install -o robert -g robert -m 0644 "${SOURCE_DISPATCHER_UNIT}" "${USER_DISPATCHER_DEST}"
systemctl daemon-reload
loginctl enable-linger robert
systemctl start "user@$(id -u robert).service"
# ``enable --now`` does not rerun an already-active oneshot unit.  Restart is
# deliberate: it applies the newly installed cgroup hierarchy immediately and
# keeps the service enabled for the next boot.
systemctl enable greenran-cgroup-delegation.service
systemctl restart greenran-cgroup-delegation.service
runuser -u robert -- /usr/bin/python3 "${PROJECT_ROOT}/scripts/verify_greenran_cgroup_delegation.py" --require-attach
readonly USER_RUNTIME="/run/user/$(id -u robert)"
runuser -u robert -- env \
  XDG_RUNTIME_DIR="${USER_RUNTIME}" \
  DBUS_SESSION_BUS_ADDRESS="unix:path=${USER_RUNTIME}/bus" \
  /usr/bin/systemctl --user daemon-reload
# The historical article collector is intentionally manual-only.  It can
# restart after reboot and occupy the shared GreenRAN cgroup, preventing the
# dispatcher from proving exclusive ownership of a new campaign.  Preserve
# its unit and all data; only remove automatic startup.
runuser -u robert -- env \
  XDG_RUNTIME_DIR="${USER_RUNTIME}" \
  DBUS_SESSION_BUS_ADDRESS="unix:path=${USER_RUNTIME}/bus" \
  /usr/bin/systemctl --user disable --now greenran-tasam-collection.service 2>/dev/null || true
runuser -u robert -- env \
  XDG_RUNTIME_DIR="${USER_RUNTIME}" \
  DBUS_SESSION_BUS_ADDRESS="unix:path=${USER_RUNTIME}/bus" \
  /usr/bin/systemctl --user enable --now greenran-campaign-dispatcher.service

printf '\nBootstrap concluído. As campanhas agora rodam sem sudo.\n'
