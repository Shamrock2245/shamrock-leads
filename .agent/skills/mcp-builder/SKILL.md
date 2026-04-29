---
name: mcp-builder
description: "Build high-quality MCP servers that connect LLMs to external services. Use when creating or improving MCP servers, tools, or integrations. Covers four-phase workflow: research, implementation, testing, evaluation."
source: "anthropics/skills/mcp-builder"
---

# MCP Builder

Create MCP (Model Context Protocol) servers that enable LLMs to interact with external services.

## Phase 1: Research

### MCP Design Principles
- **API Coverage vs Workflow Tools**: Balance comprehensive endpoint coverage with specialized workflows
- **Tool Naming**: Clear, descriptive names with consistent prefixes (e.g., `github_create_issue`)
- **Context Management**: Concise descriptions, pagination support, focused data
- **Actionable Error Messages**: Guide agents toward solutions

### Documentation
- MCP spec: https://modelcontextprotocol.io/sitemap.xml (append `.md` for markdown)
- TypeScript SDK: https://raw.githubusercontent.com/modelcontextprotocol/typescript-sdk/main/README.md
- Python SDK: https://raw.githubusercontent.com/modelcontextprotocol/python-sdk/main/README.md

## Phase 2: Implementation

### Tool Design
- **Input Schema**: Zod (TS) or Pydantic (Python) with constraints and descriptions
- **Output Schema**: Define `outputSchema` for structured data
- **Annotations**: `readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`
- **Implementation**: Async/await, proper error handling, pagination support

### Transport
- **Streamable HTTP**: For remote servers (stateless JSON)
- **stdio**: For local servers

## Phase 3: Testing
- TypeScript: `npm run build` then `npx @modelcontextprotocol/inspector`
- Python: `python -m py_compile your_server.py` then MCP Inspector

## Phase 4: Evaluation
Create 10 independent, read-only, complex questions with verifiable answers in XML format.
