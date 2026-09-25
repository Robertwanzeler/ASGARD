#!/usr/bin/env bash
# Root-only helper installed by install_greenran_cgroup_delegation.sh.
# It changes permissions only inside the cgroup of robert's user manager and
# its GreenRAN child. The latter is required by cgroup-v2 delegation rules.
set -euo pipefail

readonly CGROUP_ROOT=/sys/fs/cgroup
readonly DELEGATE_USER=robert
readonly DELEGATE_GROUP=robert
readonly REQUIRED_CONTROLLERS=(cpu memory io)
readonly GREENRAN_GROUPS=(simulator ric_xapps rapp_armd tasam collectors)
readonly GREENRAN_SLOT_IDS=(slot-a slot-b)
readonly ROOT_POINTER=/run/greenran-cgroup-root

fail() {
  printf 'ERRO: %s\n' "$*" >&2
  exit 1
}

[[ "${EUID}" -eq 0 ]] || fail "este bootstrap precisa executar como root"
[[ -f "${CGROUP_ROOT}/cgroup.controllers" ]] || fail "cgroup v2 não está disponível em ${CGROUP_ROOT}"
id "${DELEGATE_USER}" >/dev/null 2>&1 || fail "usuário ausente: ${DELEGATE_USER}"
getent group "${DELEGATE_GROUP}" >/dev/null 2>&1 || fail "grupo ausente: ${DELEGATE_GROUP}"
readonly DELEGATE_UID="$(id -u "${DELEGATE_USER}")"
readonly USER_UNIT="user@${DELEGATE_UID}.service"
readonly USER_CGROUP_REL="$(systemctl show "${USER_UNIT}" -p ControlGroup --value)"
[[ "${USER_CGROUP_REL}" == /* ]] || fail "cgroup do ${USER_UNIT} não está ativo; habilite o linger de ${DELEGATE_USER}"
readonly USER_SLICE="${CGROUP_ROOT}/user.slice"
readonly USER_UID_SLICE="${USER_SLICE}/user-${DELEGATE_UID}.slice"
readonly USER_CGROUP_ROOT="${CGROUP_ROOT}${USER_CGROUP_REL}"
readonly GREENRAN_ROOT="${USER_CGROUP_ROOT}/greenran"
[[ -d "${USER_CGROUP_ROOT}" ]] || fail "cgroup do usuário ausente: ${USER_CGROUP_ROOT}"

contains_word() {
  local needle="$1"
  local file="$2"
  tr ' ' '\n' < "${file}" | grep -Fxq "${needle}"
}

enable_controllers() {
  local target="$1"
  local controller
  local missing=()
  for controller in "${REQUIRED_CONTROLLERS[@]}"; do
    contains_word "${controller}" "${target%/cgroup.subtree_control}/cgroup.controllers" || \
      fail "controlador ${controller} indisponível em ${target%/cgroup.subtree_control}"
    if ! contains_word "${controller}" "${target}"; then
      missing+=("+${controller}")
    fi
  done
  if ((${#missing[@]})); then
    printf '%s ' "${missing[@]}" > "${target}"
  fi
}

assert_empty() {
  local group="$1"
  local pids
  pids="$(tr '\n' ' ' < "${group}/cgroup.procs" | xargs || true)"
  [[ -z "${pids}" ]] || fail "há processos ativos em ${group}; não alterei permissões: ${pids}"
}

# ``io`` is normally not enabled by systemd in the user slice.  Enable the
# required controllers only through robert's ancestry; this sets no resource
# limit for other users and makes it available to the delegated child.
for ancestor in "${USER_SLICE}" "${USER_UID_SLICE}" "${USER_CGROUP_ROOT}"; do
  [[ -d "${ancestor}" ]] || fail "ancestral cgroup ausente: ${ancestor}"
  assert_empty "${ancestor}"
  enable_controllers "${ancestor}/cgroup.subtree_control"
done

# The GreenRAN child is contained in robert's user manager. This placement is
# required by cgroup-v2's common-ancestor permission rule and does not grant
# access to other users' cgroups.
mkdir -p "${GREENRAN_ROOT}"
assert_empty "${GREENRAN_ROOT}"
for group in "${GREENRAN_GROUPS[@]}"; do
  [[ -e "${GREENRAN_ROOT}/${group}" ]] || mkdir "${GREENRAN_ROOT}/${group}"
  assert_empty "${GREENRAN_ROOT}/${group}"
done
enable_controllers "${GREENRAN_ROOT}/cgroup.subtree_control"

# Parallel feasibility arms receive a second delegated level.  Each slot has
# the same five resource groups as the serial tree, so a launcher can point
# GREENRAN_CGROUP_ROOT at one slot without ever crossing into the other.
readonly SLOTS_ROOT="${GREENRAN_ROOT}/slots"
mkdir -p "${SLOTS_ROOT}"
assert_empty "${SLOTS_ROOT}"
enable_controllers "${SLOTS_ROOT}/cgroup.subtree_control"
for slot_id in "${GREENRAN_SLOT_IDS[@]}"; do
  slot_root="${SLOTS_ROOT}/${slot_id}"
  mkdir -p "${slot_root}"
  assert_empty "${slot_root}"
  enable_controllers "${slot_root}/cgroup.subtree_control"
  for group in "${GREENRAN_GROUPS[@]}"; do
    [[ -e "${slot_root}/${group}" ]] || mkdir "${slot_root}/${group}"
    assert_empty "${slot_root}/${group}"
  done
done

# A process launched from a session scope starts below USER_CGROUP_ROOT. The
# kernel requires write access to this ancestor's cgroup.procs for migration
# into the delegated child. This is limited to robert's own user unit.
chown "${DELEGATE_USER}:${DELEGATE_GROUP}" "${USER_CGROUP_ROOT}/cgroup.procs"

# Do not recurse: these are the only directories and pseudo-files that the
# unprivileged GreenRAN launcher is allowed to manage or observe.
chown "${DELEGATE_USER}:${DELEGATE_GROUP}" "${GREENRAN_ROOT}"
chown "${DELEGATE_USER}:${DELEGATE_GROUP}" "${GREENRAN_ROOT}/cgroup.procs"
for group in "${GREENRAN_GROUPS[@]}"; do
  group_path="${GREENRAN_ROOT}/${group}"
  chown "${DELEGATE_USER}:${DELEGATE_GROUP}" "${group_path}"
  for filename in cpu.max memory.high io.weight cgroup.procs cpu.stat memory.current memory.peak io.stat; do
    [[ -e "${group_path}/${filename}" ]] || fail "arquivo cgroup ausente: ${group_path}/${filename}"
    chown "${DELEGATE_USER}:${DELEGATE_GROUP}" "${group_path}/${filename}"
  done
done

chown "${DELEGATE_USER}:${DELEGATE_GROUP}" "${SLOTS_ROOT}"
chown "${DELEGATE_USER}:${DELEGATE_GROUP}" "${SLOTS_ROOT}/cgroup.procs"
for slot_id in "${GREENRAN_SLOT_IDS[@]}"; do
  slot_root="${SLOTS_ROOT}/${slot_id}"
  chown "${DELEGATE_USER}:${DELEGATE_GROUP}" "${slot_root}"
  for filename in cgroup.procs cgroup.subtree_control; do
    [[ -e "${slot_root}/${filename}" ]] || fail "arquivo cgroup ausente: ${slot_root}/${filename}"
    chown "${DELEGATE_USER}:${DELEGATE_GROUP}" "${slot_root}/${filename}"
  done
  for group in "${GREENRAN_GROUPS[@]}"; do
    group_path="${slot_root}/${group}"
    chown "${DELEGATE_USER}:${DELEGATE_GROUP}" "${group_path}"
    for filename in cpu.max memory.high io.weight cgroup.procs cpu.stat memory.current memory.peak io.stat; do
      [[ -e "${group_path}/${filename}" ]] || fail "arquivo cgroup ausente: ${group_path}/${filename}"
      chown "${DELEGATE_USER}:${DELEGATE_GROUP}" "${group_path}/${filename}"
    done
  done
done

printf '%s\n' "${GREENRAN_ROOT}" > "${ROOT_POINTER}"
chown "${DELEGATE_USER}:${DELEGATE_GROUP}" "${ROOT_POINTER}"
chmod 0644 "${ROOT_POINTER}"

printf 'Delegação GreenRAN preparada em %s para %s.\n' "${GREENRAN_ROOT}" "${DELEGATE_USER}"
