# Prior art for small-model agents

Label: `wayfinder:research`
Status: closed
Assignee: Raquel (research agent)
Blocked by: 
Map: [New harness for Harness Arena](map.md)

## Question

What patterns work for small-model agents on document and log investigation tasks with citations and prompt-injection risk: plan-then-act, code-first extraction, retrieval, verification passes, structured outputs, tool guards. Include the kit's slides (slides/) and examples/pi. Which ideas fit a $3 / 30-minute budget for 48 tasks?

## Resolution

Findings: branch `research/prior-art`, file `research/prior-art.md` (pushed, not merged).

Ranked by expected payoff for this harness:
1. Code-enforced action guards and a tool set fixed before any data is read (AgentDojo tool filter: targeted attacks
   57.7 % → 6.8 %, utility kept). Check the known attack goals in code: recipients, URL defanging, no secrets out,
   evidence required before naming an agent.
2. Per-family workflows with the model used only for judgment, plus a generic fallback (Agentless, CodeAct, PoT).
3. Parse every distinct file once per run into chunks tagged with citation locators; BM25 + optional embeddings
   (≈ $0.02/run) instead of browsing (hybrid retrieval cut retrieval failures 49 %).
4. Verify against code checks, not "think again"; normalise answer formats (Huang et al. 2023; CRITIC).
5. Plan-then-execute with a short bounded repair loop (ReWOO: ~5× fewer tokens than ReAct).
6. Level-dependent budget caps, submit as soon as solved, handle the 403 at the spend limit.
7. Small context; Jev only to route or flag content, never to refuse (detector-refusal dropped utility to 41.5 %).
8. Self-consistency voting and a full CaMeL build: poor value at this budget.

Budget: a competitive cheap run is ≈ $0.15–0.40, i.e. 15k–40k input tokens per task. Kit: slides' trust tiers ("no
tier gives orders"); examples/pi is code-first parsing without verification or Jev.
Not verified: no injection-defence result was measured on Gemma 4; strict JSON-schema output on the pinned providers is
unchecked.
