# Plugin Architecture

## Architecture

Project Hunter plugins are extension units executed through the pipeline entry point. The plugin layer separates discovery, loading, validation, registration, lifecycle execution, and orchestration.

Plugins communicate only through `PipelineContext`. They must not call each other directly and must not assume execution order except through declared dependencies and configured ordering.

The core orchestration layer depends on `PluginManager`, not on individual plugins. Future capabilities can be added by exposing compliant plugins through configuration, built-in discovery, module paths, or package entry points.

## Plugin Contract

Every plugin must expose:

- `id`
- `name`
- `version`
- `author`
- `description`
- `category`
- `dependencies`
- `capabilities`
- `configuration_schema`
- `enabled`
- `initialize(context)`
- `validate(context)`
- `execute(context)`
- `shutdown(context)`

Metadata is represented by `PluginMetadata`. Runtime state and shared data are represented by `PipelineContext`.

## Discovery

Plugin discovery supports:

- Built-in plugins passed by the orchestrator or tests.
- Configured module paths in `configs/plugins.yaml`.
- Future external plugins exposed through the `hunter.plugins` package entry point group.

The discovery layer does not hardcode plugin registrations.

## Lifecycle

The lifecycle is:

1. Load
2. Validate
3. Initialize
4. Execute
5. Shutdown

Shutdown runs in reverse execution order. Lifecycle failures raise plugin-specific exceptions and preserve the original exception as context.

## Registration

The registry provides central plugin lookup:

- By plugin id.
- By capability.
- By category.
- By version.
- By declared dependencies.

Duplicate plugin ids are rejected.

## Validation

Validation rejects invalid plugins before execution.

The validator checks:

- Required metadata.
- Semantic version format.
- Declared capabilities.
- Duplicate ids.
- Missing dependencies.
- Minimum dependency versions.
- Cyclic dependencies.

Invalid plugins must not load into the registry.

## Adding New Plugins

New plugins must implement the plugin contract and declare complete metadata.

A plugin may be added as:

- A built-in plugin supplied to `PluginManager.load`.
- A configured module path in `configs/plugins.yaml`.
- A package exposing the `hunter.plugins` entry point group.

New plugins must exchange data through `PipelineContext`, declare dependencies explicitly, remain independently testable, and avoid direct coupling to other plugins.

