"""Legacy entrypoint: use the stricter network-isolated Kilo repair canary.

The gateway variant subsumes the old workspace-only canary and retains the
independent broker-evidence verification without a duplicated implementation.
"""

from __future__ import annotations

from kilo_gateway_repair_canary import main

if __name__ == "__main__":
    main()
