# Hunter Local Reviewer Operations

Normal PR review is automatic. SSH is only for diagnostics or recovery.

## Status

```bash
curl -fsS http://127.0.0.1:11434/api/tags >/dev/null && echo 'Ollama: online'
ollama list | grep 'qwen2.5-coder:7b'
cd ~/actions-runner-hunter-reviewer && ./svc.sh status
```

## Bootstrap or repair

From the Project Hunter clone:

```bash
bash scripts/bootstrap_hunter_review_runner.sh
```

The script obtains a one-time runner registration token through authenticated `gh api`; it never prints or commits that token. The dedicated runner uses label `hunter-reviewer`.

## Restart and logs

```bash
cd ~/actions-runner-hunter-reviewer
./svc.sh stop && ./svc.sh start
./svc.sh status
ls -1t _diag/Runner_*.log | head -1 | xargs tail -80
```

## How a review is triggered

Nothing here is triggered by hand in normal operation. The trusted default-branch
Hunter Reviewer Collector dispatches `hunter-local-reviewer.yml` on the default
branch with the candidate's PR number, exact head, claims digest, and a
per-invocation `correlation_id` that the run renders as its run name. The
collector recognises its own run by that name, treats the run as acknowledged
only once a runner has actually picked it up, and reads the verdict from the
run's `hunter-local-review-<pr>-<head>` artifact.

A run that never starts within the acknowledgement budget, fails, or publishes no
usable `hunter.local-review.v1` result is reviewer unavailability, not a
candidate defect: collection simply continues to the next reviewer in the pool.
This reviewer is triage-only, so its verdict is recorded as evidence and never
ends the authority search.

A manual diagnostic review should be dispatched through the trusted `Hunter Local Reviewer` workflow. Do not run candidate code over SSH.
