# ADR-0017: Cross-cutting lessons

## Status

Accepted (retrospective — records lessons that shaped multiple decisions rather than a single choice)

## Context

Several lessons recurred across multiple modules and reviews and informed decisions throughout the project rather than belonging to any single one. They are recorded here so they can be referenced as shared rationale.

## Decision

Treat the following as standing lessons that inform how work is reviewed, tested, and decided across the project.

## Consequences

- **Two independent adversarial reviews consistently outperformed one.** Across three review rounds on `quote_match.py` alone, different reviewers found genuinely different, non-overlapping bugs, and at least one review (on the domain check) was simply wrong about there being no exploit. Confidence in a review's reasoning is not evidence; running the claimed counterexample is.
- **A documented "we decided not to fix this" limitation got resolved later, not by trying harder at the same approach, but by realizing the earlier fixes were proxies for something specific** (real span overlap), and building the real thing. The right response to "I can't fix this" is sometimes "come back once I understand what I was actually approximating."
- **The project's own central mistake was made at least twice while building the tool meant to prevent it** — once in `quote_match.py` (the numeric gate bug) and once in `tag_schema.py` (the Bucket C label). Both were caught by the same discipline (external, adversarial review) applied to the project's own work, not just to AI-generated climate claims. That symmetry is the actual proof the principle works, not an embarrassing footnote.
- **A genuinely good architectural decision (adding web search) revealed a second, more subtle gap once built**, the same way a fix often surfaces the next thing to check: search gave the model real candidate URLs, but nothing enforced that the model's final proposal actually came from that list rather than memory. A prompt instruction is not a guarantee — the same lesson as everything else, discovered one layer further up the pipeline than the original hallucination problem it was meant to fix.
- **A broken terminal command is not a lost commit.** Typing a long, multi-line, punctuation-heavy commit message directly inline in a shell broke mid-command (an unterminated quote). Nothing was lost — `git status` confirmed the staged changes were untouched, since a failed `git commit` invocation never reaches the point of creating a commit object. The fix to keep using: write the message to a file first (a heredoc, or a text editor) and commit with `git commit -F file.txt`, or keep messages to a single line with no special punctuation if using `-m` inline. Long, detailed reasoning belongs in a real file either way.
- **Mocked tests and live runs catch genuinely different classes of error, and neither substitutes for the other.** Every test for `page_fetch.py`, however thorough, used hand-built mock responses that always included a `Content-Length` header, because that is what the original design assumed every real server would send. A real, live run against a real server (TSMC's actual press release, served with chunked transfer encoding) surfaced a real-world HTTP pattern the entire mocked suite was structurally blind to — not through any flaw in the tests, but because the tests could only ever exercise the assumptions baked into their own fixtures. The deterministic suite proves the logic does what it is designed to do; only a live run proves the design's assumptions about the outside world were correct. This project's three live-run failures across one evening (a missing key, a removed-then-replaced search provider, and the chunked-encoding gap) were three genuine bugs the mocked suite, no matter how exhaustive, could never have found on its own.
