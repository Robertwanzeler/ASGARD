#!/usr/bin/env bash
set -u

runtime_root="${1:-}"
failures=0

ok() { printf '[OK]   %s\n' "$1"; }
warn() { printf '[WARN] %s\n' "$1"; }
fail() { printf '[FAIL] %s\n' "$1"; failures=$((failures + 1)); }

check_command() {
  local command_name="$1"
  if command -v "$command_name" >/dev/null 2>&1; then
    ok "$command_name: $(command -v "$command_name")"
  else
    fail "$command_name não encontrado"
  fi
}

check_path() {
  local path="$1"
  local description="$2"
  if [ -e "$path" ]; then
    ok "$description: $path"
  else
    fail "$description ausente: $path"
  fi
}

check_package() {
  local package_name="$1"
  if command -v dpkg-query >/dev/null 2>&1 && dpkg-query -W -f='${Status}' "$package_name" 2>/dev/null | grep -q 'install ok installed'; then
    ok "pacote $package_name instalado"
  else
    fail "pacote $package_name não encontrado"
  fi
}

printf '%s\n' 'Verificação somente leitura do ambiente ns-3/FlexRIC'

for command_name in git g++ cmake ninja make pkg-config; do
  check_command "$command_name"
done

if command -v python3 >/dev/null 2>&1; then
  ok "python3: $(python3 --version 2>&1)"
else
  fail 'python3 não encontrado'
fi

for command_name in gdb valgrind; do
  if command -v "$command_name" >/dev/null 2>&1; then
    ok "$command_name disponível"
  else
    warn "$command_name não encontrado; debug avançado ficará indisponível"
  fi
done

if command -v dpkg-query >/dev/null 2>&1; then
  for package_name in build-essential git g++ cmake ninja-build python3 libsctp-dev libboost-all-dev; do
    check_package "$package_name"
  done
  for package_name in libgsl-dev libxml2-dev libsqlite3-dev libeigen3-dev gdb valgrind; do
    if dpkg-query -W -f='${Status}' "$package_name" 2>/dev/null | grep -q 'install ok installed'; then
      ok "pacote opcional $package_name instalado"
    else
      warn "pacote opcional $package_name não encontrado"
    fi
  done
fi

if command -v docker >/dev/null 2>&1; then
  ok 'docker disponível (opcional)'
else
  warn 'docker não encontrado (opcional)'
fi

if [ -n "$runtime_root" ]; then
  check_path "$runtime_root/flexric" 'clone FlexRIC'
  check_path "$runtime_root/flexric/build/examples/ric/nearRT-RIC" 'binário nearRT-RIC'
  check_path "$runtime_root/ns-O-RAN-flexric/mmwave-LENA-oran" 'checkout ns-3 O-RAN'
  check_path "$runtime_root/ns-O-RAN-flexric/mmwave-LENA-oran/ns3" 'wrapper ns3'

  if [ -d "$runtime_root/flexric/.git" ] || [ -f "$runtime_root/flexric/.git" ]; then
    if git -C "$runtime_root/flexric" status --short >/dev/null 2>&1; then
      ok 'FlexRIC é um checkout Git válido'
    else
      fail 'FlexRIC não pode ser consultado pelo Git'
    fi
  fi

  if [ -d "$runtime_root/ns-O-RAN-flexric/.git" ] || [ -f "$runtime_root/ns-O-RAN-flexric/.git" ]; then
    if git -C "$runtime_root/ns-O-RAN-flexric" submodule status >/dev/null 2>&1; then
      ok 'submódulos ns-O-RAN consultáveis'
    else
      fail 'submódulos ns-O-RAN não estão prontos'
    fi
  fi
fi

if [ "$failures" -eq 0 ]; then
  printf '%s\n' 'Resultado: verificação concluída sem falhas.'
else
  printf 'Resultado: %s falha(s) encontrada(s).\n' "$failures"
fi

exit "$failures"
