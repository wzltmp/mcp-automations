# MCP + n8n Automation Library

A small library of practical AI automations: a custom MCP server in Python that exposes useful tools, plus a handful of n8n workflows that wire them into real business processes. Targets "I can ship integrations" — the core skill of an Applied AI / Forward Deployed engineer.

## Why this project
- **MCP** (Model Context Protocol) is the standard for connecting LLMs to tools. By Feb 2026 the protocol hit ~97M monthly SDK downloads — knowing how to build a server, not just consume one, is rare and highly valued.
- **n8n** is the most-used open-source automation platform — many companies actually use it internally.
- Together they show you can think about agents AND the surrounding plumbing.

## Stack
- **MCP server**: Python with the official `mcp` SDK
- **n8n**: self-host with Docker, or use n8n.cloud free tier
- **LLM**: Claude (Claude Code can connect to your MCP server directly)
- **APIs to integrate**: pick 2-3 the user (or the recruiter's company) might care about — e.g., HubSpot, Notion, Gmail, Slack, Google Sheets

## Build plan (target: 1 weekend, ~2 days)

### Day 1 — MCP server
1. Read the MCP Python SDK quickstart. 30 min.
2. Build 3-4 useful tools on a single MCP server:
   - `summarize_url(url)` — fetch a page, return a clean 5-bullet summary
   - `lead_enrichment(email_or_domain)` — pull public company info via a free API
   - `repurpose_content(text, format)` — turn a blog post into a tweet thread / LinkedIn post / newsletter
   - `daily_digest(topic)` — search the web and produce a 200-word digest
   4-5 hrs.
3. Test by connecting Claude Code to your local MCP server. 1 hr.

### Day 2 — n8n workflows
4. Run n8n locally with Docker. 30 min.
5. Build 6 workflows that call your MCP server (or the underlying APIs directly):
   - New lead in HubSpot → enrich → write a draft outreach email → save as draft
   - New blog post in Notion → repurpose into 3 social posts → save to Drafts
   - Daily 8am: fetch news on `{topics}` → email me a digest
   - Inbound support email → triage (intent + priority) → route to right inbox
   - New row in Sheets → generate a personalized email → save as draft
   - Slack `/summarize <url>` command → return summary in-thread
   6-8 hrs.
6. Export each workflow as JSON and check it into the repo. 30 min.

### Polish
7. Write the README with screenshots and a 30-second demo gif per workflow.
8. Add a `docker-compose.yml` that starts n8n + your MCP server together.

## File layout
```
mcp-n8n-automations/
├── README.md
├── requirements.txt
├── docker-compose.yml
├── .env.example
├── mcp_server/
│   ├── __init__.py
│   ├── server.py             # registers tools
│   ├── tools/
│   │   ├── summarize.py
│   │   ├── enrich.py
│   │   ├── repurpose.py
│   │   └── digest.py
├── n8n_workflows/
│   ├── 01_lead_enrichment.json
│   ├── 02_blog_repurpose.json
│   ├── 03_daily_digest.json
│   ├── 04_support_triage.json
│   ├── 05_sheets_outreach.json
│   └── 06_slack_summarize.json
└── tests/
    └── test_tools.py
```

## Interview talking points (real ones, after you build it)
- "I made the MCP server return structured outputs with Pydantic models — saved a ton of prompt engineering downstream."
- "n8n's biggest gotcha was credential scoping — I had to use n8n's built-in credential vault, not env vars in the workflows, to keep the JSON exports clean."
- "The triage workflow uses two-stage classification: a cheap haiku call for intent, then only the urgent ones go through a sonnet call for response drafting. ~80% cost reduction vs. a single big call."
- "I learned the hard way that long-running n8n workflows need explicit `wait` nodes for rate limits; otherwise HubSpot 429s cascade."

## What "done" looks like
- Public GitHub with README + workflow JSONs + MCP server code
- 1-2 short GIFs/videos of workflows running
- A `docker-compose up` that brings the whole stack up in one command
