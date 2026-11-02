"""Dependency-free runner probe entry point for a bare macOS GitHub runner."""

import importlib.util
from pathlib import Path


def main() -> None:
    module_path = Path(__file__).resolve().parents[1] / "src" / "hunter" / "automation" / "mac_bridge_probe.py"
    spec = importlib.util.spec_from_file_location("hunter_mac_bridge_probe_standalone", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("trusted probe module unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.main()


if __name__ == "__main__":
    main()
