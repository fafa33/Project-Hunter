from __future__ import annotations

from collections.abc import Iterable

from hunter.execution.identity import IntelligenceIdentityFactory
from hunter.intelligence.engines.contracts import IntelligenceEngine
from hunter.intelligence.engines.exceptions import IntelligenceEngineExecutionError
from hunter.intelligence.intelligence import Intelligence
from hunter.intelligence.validator import IntelligenceValidator
from hunter.plugins.contracts import PipelineContext


class EngineRunner:
    def __init__(
        self,
        validator: IntelligenceValidator | None = None,
        identity_factory: IntelligenceIdentityFactory | None = None,
    ) -> None:
        self._validator = validator or IntelligenceValidator()
        self._identity_factory = identity_factory or IntelligenceIdentityFactory()

    def run(self, engines: Iterable[IntelligenceEngine], context: PipelineContext) -> list[Intelligence]:
        emitted: list[Intelligence] = []
        for engine in sorted(engines, key=lambda item: (-item.priority, item.id)):
            emitted.append(self.run_one(engine, context))
        return emitted

    def run_one(self, engine: IntelligenceEngine, context: PipelineContext) -> Intelligence:
        try:
            if not engine.health_check():
                raise IntelligenceEngineExecutionError(f"Intelligence engine health check failed: {engine.id}")
            engine.validate(context)
            collected = engine.collect(context)
            analysis = engine.analyze(context, collected)
            intelligence = engine.generate_intelligence(context, analysis)
            run = context.ensure_run(engine_manifest=_engine_manifest(engine))
            intelligence = self._identity_factory.stabilize(intelligence, run, engine_version=engine.version)
            self._validator.validate(intelligence)
            context.emit_intelligence(intelligence)
        except IntelligenceEngineExecutionError:
            raise
        except Exception as exc:  # noqa: BLE001
            msg = f"Intelligence engine execution failed: {engine.id}"
            raise IntelligenceEngineExecutionError(msg) from exc
        return intelligence


def _engine_manifest(engine: IntelligenceEngine) -> dict[str, object]:
    return {
        "id": engine.id,
        "name": engine.name,
        "category": engine.category,
        "version": engine.version,
        "priority": engine.priority,
        "required_inputs": engine.required_inputs,
        "produced_outputs": engine.produced_outputs,
        "capabilities": engine.capabilities,
    }
