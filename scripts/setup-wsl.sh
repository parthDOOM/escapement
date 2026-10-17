#!/usr/bin/env bash
# Reproducible local environment for Escapement (WSL2 / Linux, x86-64).
# Installs into $EDA_ROOT (default ~/eda), on the Linux filesystem:
#   - OSS CAD Suite, pinned release (yosys, iverilog, verilator, sby, ...)
#   - Python 3.11 venv (matches CI) with the test requirements (cocotb)
# Idempotent: re-running skips what is already present. Versions are recorded in
# scripts/versions.txt. Usage:  bash scripts/setup-wsl.sh  then  source ~/eda/env.sh
set -euo pipefail

OSS_CAD_RELEASE=2026-10-04
OSS_CAD_ASSET=oss-cad-suite-linux-x64-20261004.tgz
PYTHON_VERSION=3.11
EDA_ROOT=${EDA_ROOT:-$HOME/eda}
REPO=$(cd "$(dirname "$0")/.." && pwd)

mkdir -p "$EDA_ROOT"

if [ ! -x "$EDA_ROOT/oss-cad-suite/bin/yosys" ]; then
  echo "== OSS CAD Suite $OSS_CAD_RELEASE"
  curl -fsSL -o "$EDA_ROOT/oss.tgz" \
    "https://github.com/YosysHQ/oss-cad-suite-build/releases/download/$OSS_CAD_RELEASE/$OSS_CAD_ASSET"
  tar -C "$EDA_ROOT" -xzf "$EDA_ROOT/oss.tgz"
  rm "$EDA_ROOT/oss.tgz"
fi

if ! command -v uv >/dev/null 2>&1 && [ ! -x "$HOME/.local/bin/uv" ]; then
  echo "== uv (via pipx)"
  pipx install uv
fi
UV=$(command -v uv || echo "$HOME/.local/bin/uv")

if [ ! -x "$EDA_ROOT/venv/bin/python" ]; then
  echo "== Python $PYTHON_VERSION venv"
  "$UV" python install "$PYTHON_VERSION"
  "$UV" venv -q -p "$PYTHON_VERSION" "$EDA_ROOT/venv"
fi
"$UV" pip install -q -p "$EDA_ROOT/venv/bin/python" -r "$REPO/test/requirements.txt"

# OSS CAD Suite's iverilog/vvp wrappers force PYTHONHOME to its bundled Python
# and cocotb build, which breaks the venv's pinned cocotb. Use the distro
# iverilog for cocotb simulation, as CI does (apt iverilog + pip cocotb).
if [ ! -x /usr/bin/iverilog ]; then
  echo "== distro iverilog (for cocotb)"
  sudo apt-get update -qq && sudo apt-get install -y -qq iverilog
fi
mkdir -p "$EDA_ROOT/bin"
ln -sf /usr/bin/iverilog "$EDA_ROOT/bin/iverilog"
ln -sf /usr/bin/vvp "$EDA_ROOT/bin/vvp"

cat > "$EDA_ROOT/env.sh" <<EOF
# source this file: venv, distro iverilog shims, then OSS CAD Suite (yosys, sby, verilator)
export PATH="$EDA_ROOT/venv/bin:$EDA_ROOT/bin:$EDA_ROOT/oss-cad-suite/bin:\$PATH"
EOF

# shellcheck disable=SC1091
source "$EDA_ROOT/env.sh"
echo "== versions"
yosys -V
iverilog -V 2>&1 | head -1 || true  # head closes the pipe early (SIGPIPE under pipefail)
verilator --version
python --version
python -c "import cocotb; print('cocotb', cocotb.__version__)"
