#!/usr/bin/env bash
# Local harden that reproduces the CI gds job (tt-gds-action@3412659) inside WSL/Linux,
# using every local core instead of a 2-vCPU runner (docs/05-TOOLCHAIN.md, D-23).
#
#   scripts/local-harden.sh [--tag NAME] [--to STEP_ID] [KEY=JSON_VALUE ...]
#
# Same inputs as CI: tt-support-tools d66cf17, IHP-Open-PDK 2bbec755, LibreLane 3.1.0.dev3
# in its official container (--dockerized). KEY=VALUE pairs patch the merged LibreLane config
# for experiments only, e.g.  scripts/local-harden.sh --tag hold PL_RESIZER_HOLD_SLACK_MARGIN=0.05
# --to stops after a step, e.g. --to OpenROAD.ResizerTimingPostCTS for a quick A/B of
# placement, CTS, power and hold repair (~10 min) without routing.
# Results: runs/<tag>/ (gitignored); metrics summary printed at the end.
set -euo pipefail

TOOLS_REF=d66cf179e7bc4d296362ab7e2e3b344dc3c4f665
PDK_REV=2bbec755dc67ca3db0261c3d6163e15735d66710
LIBRELANE=3.1.0.dev3
EDA="${EDA:-$HOME/eda}"
export PDK_ROOT="${PDK_ROOT:-$EDA/pdk-ihp-$PDK_REV}"
export PDK=ihp-sg13cmos5l
VENV="$EDA/harden-venv"

cd "$(dirname "$0")/.."
TAG=wokwi
if [[ "${1:-}" == "--tag" ]]; then TAG="$2"; shift 2; fi
TO=()
if [[ "${1:-}" == "--to" ]]; then TO=(--to "$2"); shift 2; fi

# --- tools (same checkout path as the action: ./tt)
if [[ ! -d tt/.git ]] || [[ "$(git -C tt rev-parse HEAD)" != "$TOOLS_REF" ]]; then
  rm -rf tt
  git init -q tt
  git -C tt fetch -q --depth 1 https://github.com/TinyTapeout/tt-support-tools "$TOOLS_REF"
  git -C tt checkout -q FETCH_HEAD
fi

# --- PDK (same script logic as tt-gds-action install_sg13cmos5l.sh)
if [[ ! -f "$PDK_ROOT/ihp-sg13cmos5l/SOURCES" ]]; then
  mkdir -p "$PDK_ROOT"
  git -C "$PDK_ROOT" init -q
  git -C "$PDK_ROOT" fetch -q --depth 1 https://github.com/IHP-GmbH/IHP-Open-PDK.git "$PDK_REV"
  git -C "$PDK_ROOT" checkout -q FETCH_HEAD
  echo "IHP-Open-PDK $PDK_REV" > "$PDK_ROOT/ihp-sg13cmos5l/SOURCES"
fi

# --- python env (tt requirements + pinned LibreLane)
if [[ ! -x "$VENV/bin/python" ]]; then
  # CI uses Python 3.11 (actions/setup-python); reuse the 3.11 from scripts/setup-wsl.sh.
  "$EDA/venv/bin/python3.11" -m venv "$VENV"
  "$VENV/bin/pip" install -q -r tt/requirements.txt "librelane==$LIBRELANE"
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"

./tt/tt_tool.py --create-user-config --ihp

# --- local overrides: all cores, plus any experiment keys
python3 - "$@" <<'EOF'
import json, os, sys
p = "src/config_merged.json"
c = json.load(open(p))
c["OPENROAD_THREADS"] = os.cpu_count()
for kv in sys.argv[1:]:
    k, v = kv.split("=", 1)
    try:
        c[k] = json.loads(v)
    except json.JSONDecodeError:
        c[k] = v
    print(f"override {k} = {c[k]!r}")
json.dump(c, open(p, "w"), indent=2)
EOF

rm -rf "runs/$TAG"
mkdir -p "runs/$TAG"           # LibreLane requires --force-run-dir to exist (tt_tool does the same)
start=$(date +%s)
python -m librelane --pdk-root "$PDK_ROOT" --docker-no-tty --dockerized \
  --pdk ihp-sg13cmos5l --manual-pdk \
  --run-tag "$TAG" --force-run-dir "runs/$TAG" "${TO[@]}" src/config_merged.json
echo "harden wall time: $(( ($(date +%s) - start) / 60 )) min"
[[ ${#TO[@]} -gt 0 ]] && exit 0

python3 - "runs/$TAG" <<'EOF'
import json, sys
m = json.load(open(f"{sys.argv[1]}/final/metrics.json"))
for k in ["design__instance__count__stdcell", "design__instance__utilization",
          "design__instance__count__hold_buffer", "timing__setup__ws__corner:nom_slow_1p08V_125C",
          "timing__hold__ws__corner:nom_fast_1p32V_m40C", "route__wirelength",
          "route__drc_errors", "design__max_slew_violation__count__corner:nom_slow_1p08V_125C",
          "design__lvs_error__count", "antenna__violating__nets"]:
    print(f"{k:60s} {m.get(k)}")
EOF
