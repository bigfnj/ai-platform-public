# Tools in CX Agent Studio — every supported tool type

> Source: docs.cloud.google.com/gemini-enterprise-cx/cx-agent-studio/tool
> As of: 2026-08 · Verified: 2026-08-18 · Status: GA except Google Maps tools (Preview)

## What is a tool in CX Agent Studio?

A tool connects an agent to an external system or to inline code. Google's definition is that
tools let agents "interact with other systems to fetch, update, format, or analyze
information." Tools are what make the difference between an agent that describes a return policy
and an agent that actually processes the return, which is the whole basis of Google's "agentic"
positioning for GECX.

## Which tool types does CX Agent Studio support?

Seventeen tool types are documented. The connector-style tools are **Confluence tools**,
**Jira tools**, **Salesforce tools**, **Service Now tools**, and **SharePoint tools**, each
connecting the agent to an instance of that system. The knowledge-style tools are **Data store
tools** ("AI-generated agent responses based on website content and uploaded data"), **File
search tools** ("upload file or provide RAG knowledge base to an agent"), **Google Search
tools** (grounding with Google Search), and **Google Maps tools** (Preview status).

The code-and-API tools are **OpenAPI tools** (connect to an external API using an OpenAPI
schema), **Python code tools** (supply Python as a tool), **Client function tools** ("code tools
that are executed on the client side, not by the agent"), **MCP tools** (connect to an MCP
server), and **Integration Connector tools** (tools using your configured Connections).

The remaining three are structural: **Agent as a tool** ("reuse capabilities of agents without
handing off to another agent"), **System tools** (built-in tools for common tasks), and
**Widget tools** ("flexible widget tools to create rich user interactions").

## Agent-as-a-tool versus handoff — a distinction that trips people up

**Agent as a tool** invokes another agent and returns the result to the calling agent, which
keeps control of the conversation. A **handoff rule** transfers control to the other agent.
Choose agent-as-a-tool when you need an answer from a specialist and want to continue; choose
handoff when the other agent should own the rest of the interaction. Getting this backwards
produces an agent that either narrates its own internal delegation or silently loses the thread.

## How tools are named and described, and why the description matters most

Two rules govern tool authoring. Use **snake case** for the tool name, and make names
"semantically meaningful and relevant to the task they perform." Then: **"tool descriptions are
supplied to agent models"**, so Google's instruction is to "always provide high quality
descriptions of your tools." The description is not documentation for humans — it is the text the
model reads to decide whether to call the tool. A vague description is a functional bug, not a
tidiness issue.

## Testing a tool in isolation

Tools can be tested directly with a JSON input payload, for example:

```json
{
  "place": "automobile repair center",
  "city": "austin texas"
}
```

Test the tool before wiring it into an instruction. A tool that fails in isolation will present
as a reasoning failure once the agent is in the loop, and you will debug the wrong layer.

## Documented limits on tools

The tools page states no specific quotas or system limits; it defers to the general quotas
documentation. Do not assert a maximum tool count per agent — verify it against the current
quotas page for your project and region.
