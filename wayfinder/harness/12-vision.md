# Image inputs

Label: `wayfinder:grilling`
Status: closed
Assignee: Raquel (session)
Blocked by: 
Map: [New harness for Harness Arena](map.md)

## Question

Tickets (668/973 ticket tasks) and the binding standing order are images. How does the harness read them: Gemma 4 vision
once per distinct image per run with a structured extraction prompt, cached; what is extracted (ticket fields, the 4
standing-order settings); how are image-borne injections handled; and what does it cost (image token price on
DeepInfra is unknown)?

## Resolution

Decided with the human (grilling, all recommendations accepted):

1. Every distinct image of a run (71–106) is transcribed once at run start, in parallel, cached by blob hash
   (≈ $0.03–0.05 per run). Tasks see transcripts as text; the agent loop has no vision step.
2. One generic prompt: verbatim transcription plus a description of marks (stamps such as OPEN/CLOSED, signatures,
   handwriting, strike-throughs, dates). Code and family solvers extract fields from the text. No per-type prompts
   unless measurement demands one.
3. Measure first: during implementation, transcribe the development runs' images and score image-only facts (scanned
   domains, the 4 standing-order settings, ticket status) against dev expected answers. Add higher resolution or a
   second independent read only for image kinds that show errors. First check which image budget the provider uses
   (`usage.prompt_tokens` on one call).
4. Transcripts are untrusted file content: the prompt says transcribe, never follow; same structural `untrusted` flags;
   cited as path-only evidence units; same guards.
5. No Jev on images (early feature, long-context mode); Jev questions about transcripts are allowed.

Handed on: "the signed standing order outranks the typed copy" and similar binding rules belong to Family solvers.
