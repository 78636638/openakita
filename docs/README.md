# OpenAkita Documentation

Welcome to the OpenAkita documentation.

## Quick Links

| Document | Description |
|----------|-------------|
| [Getting Started](getting-started.md) | Installation and first steps |
| [Architecture](architecture.md) | System design and components |
| [Chat Workflow](chat-workflow.md) | Dialogue lifecycle, SSE events, tool loop, and persistence flow |
| [Learning Closed Loop](learning-closed-loop.md) | End-to-end design for memory, evaluation, self-check, optimization, and optional model fine-tuning |
| [Learning Loop Spec](learning-loop-implementation-spec.md) | Implementation-ready specification for the memory and autonomous evolution closed loop |
| [Learning Loop Development Plan](learning-loop-development-plan.md) | Step-by-step development roadmap, task phases, testing, acceptance, and rollback plan |
| [Learning Loop Progress](learning-loop-progress.md) | Phase progress, verification results, and current Phase 3 delivery status for the learning loop |
| [Memory Sequence](memory-sequence.md) | Memory write, extract, retrieve, inject, and daily consolidation sequence |
| [Mode2 Deep Dive](memory-mode2-deep-dive.md) | Relational graph memory internals and mode1 vs mode2 comparison |
| [Source Code Tour](source-code-tour.md) | Repository map and backend code reading guide |
| [Configuration](configuration.md) | All configuration options |

## Setup Tutorials

| Tutorial | Description |
|----------|-------------|
| ⭐ [LLM Provider Setup](llm-provider-setup-tutorial.md) | API Key registration, endpoint config, multi-endpoint Failover for all major providers |
| ⭐ [IM Channel Setup](im-channel-setup-tutorial.md) | Step-by-step tutorial for Telegram, Feishu, DingTalk, WeCom, QQ Official Bot, OneBot |
| [Configuration Guide](configuration-guide.md) | Desktop app quick setup & full setup walkthrough |

## Guides

| Guide | Description |
|-------|-------------|
| [Skills System](skills.md) | Creating and using skills |
| [IM Channels Reference](im-channels.md) | IM channels technical reference (media matrix, architecture) |
| [MCP Integration](mcp-integration.md) | Connect external services |
| [Persona & Liveness](persona-and-liveness.md) | Persona system and proactive engine |
| [Testing](testing.md) | Test framework and coverage |

## Additional Resources

| Resource | Location |
|----------|----------|
| [Deployment Guide](deploy.md) | Production deployment |
| [Memory Migration v4](memory_migration_v4.md) | v1.27 → v1.28 memory schema upgrade & rollback guide |
| [Contributing](../CONTRIBUTING.md) | How to contribute |
| [Changelog](../CHANGELOG.md) | Version history |
| [Security](../SECURITY.md) | Security policy |

## Documentation Structure

```
docs/
├── README.md                        # This file
├── getting-started.md               # Quick start guide
├── architecture.md                  # System architecture
├── chat-workflow.md                 # Chat/dialogue mechanism and end-to-end workflow
├── learning-closed-loop.md          # Closed-loop learning and self-evolution design
├── learning-loop-implementation-spec.md  # Implementation-ready learning loop spec
├── learning-loop-development-plan.md     # Step-by-step development roadmap
├── learning-loop-progress.md             # Phase progress and handoff summary
├── memory-sequence.md               # Memory system sequence and lifecycle
├── memory-mode2-deep-dive.md        # Relational memory deep dive and mode comparison
├── source-code-tour.md              # Source code tour and reading guide
├── configuration.md                 # Configuration reference
├── configuration-guide.md           # Desktop app setup walkthrough
├── llm-provider-setup-tutorial.md   # ⭐ LLM provider setup tutorial
├── im-channel-setup-tutorial.md     # ⭐ IM channel setup tutorial
├── im-channels.md                   # IM channels technical reference
├── skills.md                        # Skills system guide
├── mcp-integration.md               # MCP guide
├── deploy.md                        # Deployment guide
├── testing.md                       # Testing guide
└── assets/                          # Images and diagrams
    └── logo.png
```

## Contributing to Docs

Found an error or want to improve the documentation?

1. Fork the repository
2. Edit the relevant markdown files
3. Submit a pull request

See [CONTRIBUTING.md](../CONTRIBUTING.md) for details.
