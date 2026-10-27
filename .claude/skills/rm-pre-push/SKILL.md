---
name: rm-pre-push
description: Privacy and safety check before committing or pushing ResearchMatch to GitHub (the repo is public). Scans unpushed commits for the Databricks login email, workspace host, service-principal/endpoint/warehouse IDs and secrets, and confirms commit author emails. Use before every git push or PR, and whenever writing docs, CLAUDE.md or skills that mention Databricks paths, users or resource IDs.
---

# Pre-push check for the public ResearchMatch repo

`Sufi-to/research_match` is **public**. Databricks workspace paths embed the login
email (`/Workspace/Users/<email>/…`), so details copied from CLI output easily leak
into docs. The user's Databricks email must never appear in git; GitHub commits use
the GitHub identity email configured in git.

## Run it

```bash
.claude/skills/rm-pre-push/scripts/check_push.sh            # compares origin/main..HEAD
.claude/skills/rm-pre-push/scripts/check_push.sh origin/main main
```

It lists any match of `@gmail.com` and other email addresses, the workspace host
(`dbc-…cloud.databricks.com`), app URLs (`databricksapps.com`), long hex IDs
and secret-like strings in the added lines and commit metadata. Then it prints the
distinct author emails. It exits non-zero when it finds something.

## Fixing a hit

- **In files:** write paths as `/Workspace/Users/<you>/…` and describe principals by
  role ("the app's service principal"), not by ID.
- **Already committed but unpushed:** rewrite only the unpushed commits, e.g.
  `git filter-branch --tree-filter "sed -i … <file>" -- origin/main..<branch>`. Then
  delete `refs/original/…` and any other local branch that still points at the old
  commits, so they can't be pushed by accident. Never rewrite commits that are
  already on GitHub without the user's explicit request.
- Re-run the check until it's clean. Push only `main`, as the user's workflow does,
  and only when asked.
