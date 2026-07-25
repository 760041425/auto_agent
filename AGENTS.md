# opencode-demo

Demo project for opencode workflow automation.

## Workflow

1. Place requirement specs in `spec/` directory
2. The orchestrator agent scans specs and assigns tasks
3. Coding → Testing → PR pipeline

## Agents

- **orchestrator** — 全局任务调度，默认 agent

## Skills

- **git_auto** — 自动化 Git 操作
