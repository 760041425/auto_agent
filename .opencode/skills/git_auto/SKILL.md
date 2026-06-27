---
name: git_auto
description: 自动化 Git 操作流程，包括自动提交、分支管理、PR 创建、合并冲突检测等。当需要进行 Git 操作时使用。
---

# Git Auto

Automate Git operations in the project workflow.
Remote: https://github.com/760041425/auto_agent (origin)

## Mandatory Workflow

Before ANY git commit or push, you MUST first run the test suite and verify it passes:

```
pytest api/tests/ services/tests/ -v
```

Only proceed with git operations if all tests pass.

## Commit & Push Flow

1. **Verify tests** — `pytest api/tests/ services/tests/ -v` must pass
2. **Create branch** — `git checkout -b feature/<task-name>`
3. **Stage changes** — `git add <relevant-files>` (never add data files: `las/*.las`, `query_images/*.jpg`, `projections/*`)
4. **Review diff** — `git diff --cached --stat` to confirm no secrets or data files
5. **Commit** — Use conventional commit format:
   - `feat(api): <message>` for API features
   - `feat(web): <message>` for frontend features
   - `feat(las): <message>` for LAS processing
   - `fix: <message>` for bug fixes
   - `test: <message>` for test additions
6. **Push** — `git push -u origin feature/<task-name>`
7. **Create PR** — If `gh` CLI is available:
   ```
   gh pr create --base main --head feature/<task-name> --title "<title>" --body "<body>"
   ```
   Otherwise, output the URL: `https://github.com/760041425/auto_agent/compare/main...feature/<task-name>`

## Branch Naming Convention

- `feature/<module>-<description>` for new features
- `fix/<description>` for bug fixes

## Remote Config

- origin: https://github.com/760041425/auto_agent
- Default base branch: `main`
