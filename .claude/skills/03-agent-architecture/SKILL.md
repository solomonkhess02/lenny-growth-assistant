# Agent Architecture Skill

## Principle

Use agents where autonomous reasoning or tool selection
provides measurable value.

Do not create agents for ordinary deterministic operations.

## Separate

- deterministic application logic
- retrieval
- model interaction
- tool invocation
- content transformation

## Responsibilities

Define explicit boundaries between:

API
→ session
→ agent
→ retrieval
→ tools/skills
→ model
→ persistence

## Agent Rules

Every agent/tool must have:

- clear purpose
- defined inputs
- defined outputs
- error behavior
- bounded responsibilities

Avoid:

- recursive agents
- unnecessary agent-to-agent communication
- hidden state
- implicit tool dependencies

## Observability

Agent execution should expose:

- selected model
- selected skill/tool
- retrieval status
- errors
- latency