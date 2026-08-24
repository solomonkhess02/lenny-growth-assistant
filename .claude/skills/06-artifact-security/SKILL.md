# Artifact Security Skill

## Goal

Render model-generated artifacts safely inside the product.

## Trust Model

Treat all generated HTML/CSS as untrusted input.

The model is not a trusted author.
Conversation content is not a trusted author.
Transcript content is not a trusted author.

## Requirement

The Artifact Viewer must render Markdown documents and
complete HTML/CSS snippets beside the chat.

It must not render raw untrusted markup directly into the
application document.

## Isolation Strategy

Choose one explicit strategy:

- isolation, or
- sanitization, or
- both

Document the choice and the rationale.

Do not rely on an undocumented or implicit strategy.

## Permit / Block

The viewer must have a stated policy covering what is:

- permitted
- blocked
- stripped or neutralized

An evaluator should be able to read the policy and
understand why each decision was made.

## Failure Behavior

If an artifact cannot be rendered safely:

- do not render it
- surface the reason to the user
- log the event

Never silently degrade to unsafe rendering.

## Verification

Test rendering with:

- a benign Markdown artifact
- a benign HTML/CSS artifact
- an artifact containing script content
- an artifact containing external resource references
- a malformed or truncated artifact
