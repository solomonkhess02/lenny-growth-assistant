# RAG Grounding Skill

## Goal

Build a trustworthy transcript-grounded knowledge system.

## Data Pipeline

Source
→ ingestion
→ cleaning
→ metadata extraction
→ chunking
→ indexing
→ retrieval
→ reranking/filtering
→ context construction
→ generation
→ citation

## Metadata

Every chunk should preserve:

- source_id
- source_title
- speaker
- source_url
- transcript_id
- chunk_id
- publication date when available

## Chunking

Choose chunk size based on semantic coherence.

Avoid:
- arbitrary splitting
- losing speaker attribution
- destroying context

Document the rationale.

## Retrieval

Retrieve evidence rather than simply retrieving similar text.

Consider:
- similarity
- metadata
- source relevance
- redundancy

## Grounding Rules

The assistant must:

- answer from available evidence
- identify supporting sources
- distinguish evidence from inference
- avoid fabricated citations
- avoid fabricated quotes

## Insufficient Evidence

If evidence is insufficient:

Do not hallucinate.

Tell the user the available transcript material
does not adequately support the answer.

## Follow-up Questions

Preserve session context.

Do not leak context across sessions.

## Evaluation

Test:

- answerable question
- ambiguous question
- unsupported question
- follow-up question
- source attribution
- irrelevant retrieval