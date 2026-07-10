# Pipeline Orchestrator

## Purpose and Responsibility

The Pipeline Orchestrator is the current execution entry point for Project Hunter's in-process analytical pipeline.

Its responsibility is coordination. It creates or receives a `PipelineContext`, optionally runs supplied Intelligence Engines, loads configured plugins, and executes the plugin lifecycle. Analytical logic belongs to engines and plugins, not to the orchestrator.

The orchestrator currently lives in `src/hunter/pipeline.py` as `PipelineOrchestrator`.

## Architectural Boundaries

The current implementation separates responsibilities as follows:

- `PipelineOrchestrator` coordinates execution order and delegates work.
- `PluginManager` loads, validates, orders, registers, and executes plugins.
- `EngineRunner` executes Intelligence Engines and emits standardized Intelligence objects.
- The Intelligence Layer defines immutable `Signal`, `Evidence`, `Observation`, `Insight`, `Confidence`, and `Intelligence` objects.
- `PipelineContext` is the shared runtime state passed through orchestration.
- Concrete Intelligence Engines own domain-specific collection, normalization, analysis, confidence, and Intelligence generation.

The orchestrator does not implement scoring, ranking, report rendering, persistence, scheduling, retries, external API access, or domain analysis.

There is no `src/hunter/orchestrator/` package in the current repository. The orchestrator implementation is the single `src/hunter/pipeline.py` module.

## Current Execution Lifecycle

`PipelineOrchestrator.run(...)` performs the current lifecycle:

1. Use the provided `PipelineContext`, or create a new empty context.
2. If `intelligence_engines` is provided, run them through `EngineRunner`.
3. Load plugins through `PluginManager.load(...)`.
4. Validate loaded plugins through `PluginManager.validate(...)`.
5. Initialize plugins through `PluginManager.initialize(...)`.
6. Execute plugins through `PluginManager.execute(...)`.
7. Shut down plugins through `PluginManager.shutdown(...)` in a `finally` block.
8. Return the same `PipelineContext`.

Intelligence Engines passed directly to `PipelineOrchestrator.run(...)` execute before plugins. Intelligence Engines can also run inside plugins when a plugin calls `EngineRunner`.

## PipelineContext

`PipelineContext` is defined in `src/hunter/plugins/contracts.py`.

It currently contains:

- `values`: mutable shared key-value state.
- `plugin_config`: plugin-specific configuration loaded by `PluginManager`.
- `events`: ordered lifecycle or test-observable event strings.
- `intelligence`: emitted standardized Intelligence objects.
- `run`: optional canonical `PipelineRun` identity for the current execution.
- `clock`: explicit execution clock used when a run must be created.

It exposes:

- `get(key, default)` for shared state lookup.
- `set(key, value)` for shared state mutation.
- `config_for(plugin_id)` for plugin configuration lookup.
- `record(event)` for lifecycle/event recording.
- `emit_intelligence(intelligence)` for Intelligence Layer emission.
- `ensure_run(...)` for creating or returning the canonical `PipelineRun`.

Dedicated services, metadata, warning collections, and error collections are not currently modeled as first-class `PipelineContext` fields. Errors are propagated as exceptions rather than stored in the context.

The context lifecycle is one orchestrator run. The current implementation does not persist context state after execution.

`PipelineRun` establishes deterministic run identity before database persistence exists. Its analytical identity is based on run type, target identity, configuration fingerprint, input fingerprint, engine manifest fingerprint, effective analytical time, parent run identity, replay source, and schema version. Runtime-only timestamps remain separate from analytical identity.

## Plugin Execution

Plugin infrastructure lives in `src/hunter/plugins/`.

Plugin discovery supports:

- Built-in plugin instances passed to the orchestrator or manager.
- Module-path factories configured in plugin configuration.
- Package entry points in the `hunter.plugins` entry point group.

Plugin configuration is loaded by `PluginConfigLoader` from `configs/plugins.yaml` by default, or from an explicit `config_path`.

`PluginManager.load(...)` performs:

1. Load plugin configuration.
2. Discover built-in, configured module-path, and entry-point plugins.
3. Filter disabled plugins.
4. Order enabled plugins.
5. Validate metadata, versions, dependencies, duplicates, and dependency cycles.
6. Register plugins in `PluginRegistry`.

Plugin lifecycle execution is:

1. `validate(context)`
2. `initialize(context)`
3. `execute(context)`
4. `shutdown(context)`

Shutdown runs in reverse plugin order.

## Intelligence Engine Execution

Intelligence Engine contracts live in `src/hunter/intelligence/engines/`.

`EngineRunner` executes engines in deterministic order by descending priority and then engine id.

For each engine, the current lifecycle is:

1. `health_check()`
2. `validate(context)`
3. `collect(context)`
4. `analyze(context, collected)`
5. `generate_intelligence(context, analysis)`
6. Stabilize generated Intelligence IDs and generated timestamps through the deterministic identity factory using the shared `PipelineRun`.
7. Validate generated Intelligence with `IntelligenceValidator`.
8. Emit the Intelligence object through `PipelineContext.emit_intelligence(...)`.

Current concrete engines include Macro, Whale, Developer, Protocol, News, Narrative, Social, and On-chain Intelligence Engines. They are also exposed as plugins through the `hunter.plugins` entry point group in `pyproject.toml`.

## Failure Handling

The current implementation uses exception propagation.

Plugin discovery failures raise `PluginDiscoveryError`. Plugin validation failures raise `PluginValidationError` or `PluginDependencyError`. Plugin lifecycle failures are wrapped in `PluginLifecycleError` with the original exception preserved as the cause.

Engine failures are wrapped in `IntelligenceEngineExecutionError`, except an existing `IntelligenceEngineExecutionError` is re-raised directly. Unhealthy engines fail before validation.

The orchestrator always attempts plugin shutdown after plugin execution begins because execution is wrapped in `try` / `finally`. If validation or initialization fails before the `try` block, shutdown is not invoked by `PipelineOrchestrator.run(...)`.

The current implementation does not distinguish optional failures from critical failures. There is no configured failure policy, retry policy, timeout handling, partial-failure recording, or warning collection.

## Determinism and Ordering

Plugin ordering is deterministic.

`PluginManager` orders plugins by:

1. Explicit `load_order` index from plugin configuration.
2. Descending configured priority.
3. Plugin id.
4. Dependency order, so available dependencies run before dependents.

Plugin validation rejects duplicate ids, missing dependencies, incompatible minimum dependency versions, and dependency cycles.

Directly supplied Intelligence Engines are ordered by:

1. Descending engine priority.
2. Engine id.

The current implementation does not implement dependency graph execution for pipeline stages, retry execution, timeout enforcement, parallel execution, or distributed execution. These are future extensions.

## Configuration

The current pipeline has no `configs/pipeline.yaml`.

The existing orchestration-related configuration file is `configs/plugins.yaml`. It currently supports:

- `enabled`: plugin enable/disable map.
- `configuration`: plugin-specific configuration passed through `PipelineContext.plugin_config`.
- `load_order`: explicit plugin ordering.
- `priorities`: configured plugin priority values.
- `module_paths`: plugin factories loaded as `module:attribute`.

Concrete engines also have domain configuration files:

- `configs/macro_engine.yaml`
- `configs/whale_engine.yaml`
- `configs/developer_engine.yaml`

These engine configuration files are consumed by their engines, not by the orchestrator itself.

## Extension Procedure

To add a new plugin without modifying core orchestration logic:

1. Implement the plugin contract from `src/hunter/plugins/contracts.py`.
2. Provide complete `PluginMetadata`.
3. Communicate only through `PipelineContext`.
4. Expose the plugin through a built-in instance, a `configs/plugins.yaml` module path, or the `hunter.plugins` entry point group.
5. Add tests for discovery, loading, lifecycle execution, validation, and expected context output.

To add a new Intelligence Engine without modifying core orchestration logic:

1. Implement the `IntelligenceEngine` contract.
2. Produce standardized Intelligence Layer objects.
3. Validate generated Intelligence through `EngineRunner`.
4. Either pass the engine to `PipelineOrchestrator.run(..., intelligence_engines=...)` or wrap it in a plugin that executes `EngineRunner`.
5. Register the plugin through the existing plugin mechanism if automatic plugin discovery is required.

## CLI Surface

The current repository does not expose pipeline CLI commands.

The following commands are not implemented and must be treated as future extensions:

- `hunter pipeline run`
- `hunter pipeline dry-run`
- `hunter pipeline list-stages`
- `hunter pipeline graph`

## Testing

Relevant automated tests include:

- `tests/test_plugins.py`, which covers plugin discovery, loading, configuration, registry lookup, deterministic dependency ordering, lifecycle execution through `PipelineOrchestrator`, duplicate detection, disabled plugins, invalid plugins, missing dependencies, and dependency version compatibility.
- `tests/test_intelligence.py`, which verifies Intelligence objects and plugin emission through `PipelineContext`.
- `tests/test_intelligence_engines.py`, which verifies engine framework behavior, `EngineRunner`, direct orchestrator engine execution before plugins, and engine execution hosted inside a plugin.
- `tests/test_macro_intelligence_engine.py`, `tests/test_whale_intelligence_engine.py`, and `tests/test_developer_intelligence_engine.py`, which verify concrete engine plugin registration and pipeline execution through configured module paths.

These tests protect the current orchestration contract: deterministic in-process execution, shared context behavior, plugin lifecycle ordering, and standardized Intelligence emission.

## Known Limitations

Current limitations include:

- No `configs/pipeline.yaml`.
- No dedicated `src/hunter/orchestrator/` package.
- No persisted pipeline-run records.
- No first-class stage model.
- No pipeline dependency graph beyond plugin dependency ordering.
- No retry policy.
- No timeout enforcement.
- No parallel execution.
- No distributed execution.
- No structured warning or error collection on `PipelineContext`.
- No optional-versus-critical failure policy.
- No pipeline CLI commands.
- No scheduler or automation layer.
- No dashboard or API surface.

## Future Extensions

Future orchestration extensions may include:

- Automation and Scheduler.
- Persistent pipeline-run records.
- Configurable dependency graphs.
- Retry policies and timeout enforcement.
- Parallel execution.
- Distributed workers.
- Dashboard and API surfaces.
- Opportunity Timing Engine integration.

These capabilities are not implemented in the current repository and must not be documented as current behavior until the code exists.
