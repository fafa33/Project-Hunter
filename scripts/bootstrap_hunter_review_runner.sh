#!/bin/bash
set -euo pipefail

REPO="${GH_REPO:-fafa33/Project-Hunter}"
LABEL="hunter-reviewer"
MODEL="qwen2.5-coder:7b"
RUNNER_DIR="${HOME}/actions-runner-hunter-reviewer"

command -v gh >/dev/null || { echo "gh is required" >&2; exit 1; }
command -v ollama >/dev/null || { echo "ollama is required" >&2; exit 1; }
curl -fsS --connect-timeout 5 --max-time 15 http://127.0.0.1:11434/api/tags >/dev/null || { echo "Ollama is not reachable" >&2; exit 1; }
ollama list | awk 'NR>1 {print $1}' | grep -Fxq "$MODEL" || { echo "Required model missing: $MODEL" >&2; exit 1; }

gh auth status >/dev/null
if [[ ! -x "$RUNNER_DIR/run.sh" ]]; then
  echo "Dedicated GitHub runner is not installed at $RUNNER_DIR" >&2
  echo "Install the GitHub Actions runner package there once, then rerun this bootstrap." >&2
  exit 2
fi

cd "$RUNNER_DIR"
if [[ ! -f .runner ]]; then
  TOKEN="$(gh api -X POST "repos/${REPO}/actions/runners/registration-token" --jq .token)"
  trap 'unset TOKEN' EXIT
  ./config.sh --unattended --url "https://github.com/${REPO}" --token "$TOKEN" --name "hunter-reviewer-mac" --labels "$LABEL" --work "_work"
fi

# A stale .runner file is not routing evidence. Verify GitHub sees this exact
# runner on this repository with the label required by the workflow.
RUNNER_OK="$(gh api "repos/${REPO}/actions/runners" --jq '[.runners[] | select(.name == "hunter-reviewer-mac" and any(.labels[]; .name == "'"$LABEL"'"))] | length')"
if [[ "$RUNNER_OK" != "1" ]]; then
  echo "Configured runner is not registered for ${REPO} with label ${LABEL}" >&2
  exit 4
fi

if [[ -x ./svc.sh ]]; then
  ./svc.sh install >/dev/null 2>&1 || true
  ./svc.sh start
  ./svc.sh status
else
  echo "svc.sh unavailable; GitHub runner package must support the macOS service helper" >&2
  exit 3
fi
