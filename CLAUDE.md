# CLAUDE.md

The instructions for working in this repository are in
**[AGENTS.md](AGENTS.md)**, which is agent-agnostic and canonical. Read it.

This file exists only so that Claude Code finds the pointer through its own
convention; keeping a second copy of the content here would guarantee the two
drift apart.

Claude Code specifics:

- Skills in `.claude/skills/` are discovered automatically, so
  `mend-record` can be invoked directly. Other agents read the same file as
  ordinary Markdown, which is why its rules are also restated in AGENTS.md.
- Work in a git worktree when making changes; `main` is protected and a pull
  request is the only route in.
