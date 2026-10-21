# Portable Hunter push environment

The repository-owned pre-push hook is a mandatory security and provenance
boundary. It invokes `python` and the repository's push-safety checks require
`ruff`, `black`, `mypy` and the declared development dependencies.
A bare Gateway/SSH non-interactive process may have neither `python` nor the
developer executables on PATH even when Git and SSH work.

For every Linux/macOS execution host, provision **once** from a checkout:

```sh
python3 scripts/hunter_push.py --setup
```

Then, from the intended clean worktree, push with:

```sh
python3 scripts/hunter_push.py origin HEAD:refs/heads/<approved-branch>
```

The launcher defaults to `~/.local/share/hunter/push-venv`, outside ephemeral
worktrees. Override with `HUNTER_PUSH_VENV` for a managed persistent location.
It injects that virtual environment into the subprocess PATH and invokes the
ordinary `git push`: **no hooks are disabled**. It never installs or modifies
tools during a push. Missing tools fail closed with a setup instruction.

On a fresh machine, rerun setup; the host-specific virtual environment is not
portable between machines or Python installations. Use the repository's
`pyproject.toml [dev]` declaration, not ad hoc package guesses. Do not check
virtual environments or credentials into Git.

After pushing, verify the remote branch SHA equals the intended signed local
HEAD with `git ls-remote --heads origin <approved-branch>`. A zero exit from
an unrelated wrapper is not proof of publication. Never force-push or bypass
the hook to fix a missing-tool error.
