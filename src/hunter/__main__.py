from __future__ import annotations

import sys

from hunter.automation.n8n_canary import main as n8n_canary_main
from hunter.cli import main as cli_main
from hunter.committee.command import main as committee_authority_main
from hunter.valuation_authority.command import main as valuation_authority_main
from hunter.valuation_evidence.command import main as valuation_evidence_main

# hunter.comparative_valuation.command (Issue #181) is deliberately NOT dispatched
# here. ADR 0026 Implementation Prerequisite 9 requires a "disabled-entry-point plan"
# and Prerequisite 10 requires independent implementation review and post-merge audit
# "before any production activation". Neither has occurred yet, so the orchestration
# module exists and is fully tested by direct construction
# (tests/test_comparative_valuation_authority_v1.py calls
# hunter.comparative_valuation.command.main(...) directly), but it is not reachable
# through the `hunter` CLI -- mirroring the identical precedent already established
# for hunter.evidence_assembly (see docs/CANONICAL_RUNTIME_ARCHITECTURE.md's Evidence
# Assembly Authority classification: "implemented ... but not part of the executable
# production runtime flow ... no CLI, scheduler, or production entry point wires it
# in"). Wiring this verb in is a separate, future, independently-reviewed change.


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "committee-authority":
        return committee_authority_main(arguments[1:])
    if arguments and arguments[0] == "valuation-evidence":
        return valuation_evidence_main(arguments[1:])
    if arguments and arguments[0] == "valuation-authority":
        return valuation_authority_main(arguments[1:])
    if arguments and arguments[0] == "n8n-canary":
        return n8n_canary_main(arguments[1:])
    return cli_main(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
