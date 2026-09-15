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

A manual diagnostic review should be dispatched through the trusted `Hunter Local Reviewer` workflow. Do not run candidate code over SSH.
