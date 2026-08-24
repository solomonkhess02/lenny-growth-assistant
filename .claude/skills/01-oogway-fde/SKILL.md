# Oogway FDE Skill

## Mission

Build the Lenny Growth Assistant as a client-ready
forward-deployed AI product.

Optimize for:

1. Customer usefulness
2. Grounding and trust
3. Reliability
4. Simplicity
5. Maintainability
6. Operational handoff
7. Demonstrability

## FDE Principles

Before implementing a major feature:

1. Identify the user problem.
2. Define the smallest useful solution.
3. Identify assumptions.
4. Identify failure modes.
5. Define acceptance criteria.
6. Implement.
7. Test.
8. Document the decision.

Do not blindly implement every possible feature.

## Scope Discipline

Prefer:
- simple architecture
- small number of dependencies
- understandable code
- explicit interfaces
- deterministic behavior where possible

Avoid:
- unnecessary microservices
- unnecessary infrastructure
- premature abstractions
- excessive agent complexity

## Requirements Traceability

Maintain awareness of:

- FastAPI
- PostgreSQL
- session isolation
- cloud LLM
- Ollama
- provider switching
- transcript ingestion
- retrieval
- source attribution
- grounded answers
- Ship 30 for 30 skill
- artifact generation
- Artifact Viewer
- HTML isolation
- logging
- resilience
- tests
- documentation
- deployment

## Decision Making

When multiple designs are possible:

Prefer the design that is:

1. easiest to explain
2. easiest to operate
3. easiest to test
4. safest
5. easiest for another engineer to extend

## Failure Behavior

Never hide failures.

Failures should be:
- detectable
- logged
- surfaced appropriately
- recoverable when possible

## Verification

Never claim a feature is complete until:

- implementation exists
- relevant tests pass
- failure modes have been tested
- documentation is updated