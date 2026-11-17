# Glossary

**Task**: one question or job in a run, with an instruction, a level and its own workspace. Graded once, on submit.

**Level**: a task's difficulty, 1 (straightforward), 2 (more hops and traps) or 3 (hard).

**Task kind**: what a task asks the agent to produce: an answer, a refusal, a draft, a message, or a state-changing
action (containment, block a domain, quarantine an agent, rotate a token, breach notification).

**Family**: the area of the workspace a task draws on (inbox messages, audit logs, agent identities, tokens, threat
intel, containment).

**Workspace**: the files a task can see, reached only through the Task API's tools.

**Surface**: one concrete rendering of a run's tasks (wording, file names). A seed fixes the tasks and answers; each run
of it gets a new surface.

**Development run** / **Held-out run**: recorded runs used to design and iterate vs. the 5 newest, kept unseen and used
only to report scores.

**Authorised action**: a state-changing call the task's instruction asks for. Any other is an out-of-scope action and
fails the task, even when refused.

**Injection**: text planted in a workspace file to steer an agent (frame an agent, trigger an action, exfiltrate data).

**Constraint failure**: a rule check that zeroes a task's score whatever the answer.
_Avoid_: "safety failure".

**Evidence unit**: the smallest citable piece of a workspace file, exactly as the grader cites it: a line, a record, a
sheet row, a paragraph, a page, or a whole image.

**Handle**: the short label (`E17`) under which the model sees an evidence unit and cites it.

**Family solver**: code written for one family that computes a task's answer from the workspace, using the model only
for bounded judgments.

**Generic loop**: the model-driven fallback that searches the workspace and records facts until it can answer.

**Notebook**: the facts the generic loop has recorded, each tied to the evidence units that support it.

**Compose**: the step that turns the gathered facts into the submission: outcome, answer, citations and any
state-changing actions.
