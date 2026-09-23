# Project Hunter Copilot Instructions

Treat repository-local authority and current GitHub state as the source of truth.

Before changing code, read `.github/instructions/project-hunter.instructions.md` and the authorities relevant to the task: the governing Issue and acceptance criteria, applicable ADRs and architecture/roadmap documents, relevant tests, and `docs/DEFECT_REGISTRY.json`.

Trace the real executable path before architecture changes. Use tests first for bug fixes and behavioral changes. Run the required Hunter verification gates before claiming completion. Never bypass governance or weaken evidence requirements. Never merge, mark Ready, deploy, or perform irreversible actions without explicit owner approval.

## Persistence rule

Never leave validated work only in an ephemeral Copilot workspace. At every meaningful checkpoint, after the required checks have the expected outcome, commit the exact state, push the intended branch, and verify that the remote HEAD exactly matches the local commit SHA before continuing substantial work.

If push fails, stop substantial new work, preserve and report the exact local commit SHA and blocker, and restore durable persistence before proceeding. If the owner explicitly instructs you not to push, obey that instruction and report that the commit remains local by owner choice.

The detailed repository-wide operating rules in `.github/instructions/project-hunter.instructions.md` remain authoritative.
