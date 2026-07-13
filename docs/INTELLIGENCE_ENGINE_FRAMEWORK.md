# Intelligence Engine Framework

Status: Experimental for v2.1.x production architecture.

The plugin-style Intelligence Engine Framework is retained for tests, automation experiments, persistence integration, and future migration work. The canonical v2.1.x production runtime is the evidence-backed Market Validation path documented in `docs/CANONICAL_RUNTIME_ARCHITECTURE.md`.

## Architecture

The Intelligence Engine Framework defines the permanent base architecture for all Project Hunter intelligence engines.

The framework provides reusable contracts, base classes, registries, factories, runners, categories, capabilities, and exceptions. It does not implement any domain intelligence logic.

Every future intelligence engine must implement the framework contract and produce canonical Intelligence Layer objects.

## Engine Contract

Every Intelligence Engine must expose:

- `id`
- `name`
- `category`
- `version`
- `priority`
- `required_inputs`
- `produced_outputs`
- `capabilities`
- `validate(context)`
- `collect(context)`
- `analyze(context, collected)`
- `generate_intelligence(context, analysis)`
- `health_check()`

The contract is represented by `IntelligenceEngine` and `EngineMetadata`.

## Base Engine

`BaseIntelligenceEngine` provides common metadata properties and requires subclasses to implement lifecycle methods.

The base class does not perform analytical work. Domain engines must provide collection, analysis, validation, health, and intelligence generation behavior.

## Factory

`EngineFactory` maps engine ids to builders.

It supports registering future engine constructors and creating engines without hardcoded orchestration changes.

## Registry

`EngineRegistry` stores engine instances and supports:

- Lookup by id.
- Lookup by category.
- Lookup by capability.
- Priority ordering.
- Metadata validation.
- Duplicate detection.

## Capability Registry

`CapabilityRegistry` stores supported engine capabilities.

Default capabilities are:

- `collect`
- `analyze`
- `generate-intelligence`
- `health-check`
- `validate`

Future capabilities may be registered without redesigning the framework.

## Category Registry

`CategoryRegistry` stores supported engine categories.

Default categories include:

- Macro.
- Whale.
- Developer.
- Protocol.
- News.
- Social.
- On-chain.
- Governance.
- Portfolio.
- AI.
- Opportunity Timing.

Future categories may be registered without modifying orchestration logic.

## Runner

`EngineRunner` executes engines deterministically by priority and id.

Execution flow:

1. `health_check`
2. `validate`
3. `collect`
4. `analyze`
5. `generate_intelligence`
6. Validate generated intelligence.
7. Emit intelligence through `PipelineContext`.

The runner does not score, rank, report, schedule, automate, or implement domain logic.

## Integration

The framework integrates with:

- Plugin Architecture through shared `PipelineContext`.
- Pipeline Orchestrator through optional engine execution.
- Intelligence Layer through validated `Intelligence` objects.

Plugins and engines may both emit Intelligence objects, but neither may bypass the Intelligence Layer.

## Future Engines

The framework supports future engines including:

- Macro.
- Whale.
- Developer.
- Protocol.
- News.
- Social.
- On-chain.
- Governance.
- Portfolio.
- AI.
- Opportunity Timing.

These engines must be added as independent implementations of the framework contract.
