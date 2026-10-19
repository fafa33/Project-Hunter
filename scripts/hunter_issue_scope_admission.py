"""Unprivileged, secret-free canonical scope admission for Issue Agent labels."""
from __future__ import annotations

import json
import os
from pathlib import Path

from hunter_issue_agent_trigger import IssueAgentTriggerError, implementation_scope_from_issue_body


def eligible(event: dict) -> bool:
    issue = event.get('issue') or {}
    try:
        implementation_scope_from_issue_body(issue.get('body') or '', task_id=f"issue-{issue['number']}")
    except (IssueAgentTriggerError, KeyError, TypeError, ValueError):
        return False
    return True


def main() -> None:
    event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text(encoding='utf-8'))
    result = 'true' if eligible(event) else 'false'
    with Path(os.environ['GITHUB_OUTPUT']).open('a', encoding='utf-8') as output:
        output.write(f'eligible={result}\n')
    print(f'Canonical issue scope admission: {result}')


if __name__ == '__main__':
    main()
