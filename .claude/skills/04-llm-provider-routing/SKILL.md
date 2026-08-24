# LLM Provider Routing Skill

## Goal

Allow model/provider switching through configuration.

## Interface

Application code should depend on a common model interface.

Examples:

CloudProvider
OllamaProvider

## Configuration

Provider selection must be configurable through:

environment/configuration

Never hardcode provider selection inside business logic.

## Failure Handling

Handle:

- missing API key
- Ollama unavailable
- model unavailable
- timeout
- malformed model response
- provider configuration error

## Observability

Log:

- provider
- model
- request duration
- success/failure

Never log:

- API keys
- secrets
- sensitive user content unnecessarily