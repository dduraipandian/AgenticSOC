# Agentic SOC

> Status: Active development · Lab-backed research / engineering project · Not production-ready

An AI agent that investigates SOC alert cases against real-looking vendor APIs, with full traces and an eval harness. Agentic SOC has ingestion, normalization, case creation (groups alerts based on hostname,and fallback to user).

![Agent loop](agent-loop.png)

- EDR        - Currently implemented ingestion from `SOCLab`
- IdP        - Planning to implement
- Cloud logs - Planning to implement

Once the case is created, Agent investigates the case using provided tools. Agent executions, and tool executions are traced in the system to view event thoughts, model output, and agent tool calls. Agent final response (verdict) is mapped as evidence with tool output to give Analyst how model's rationale is derived.

- Tool arguments are pydantic model to validate agent tool call arguments.
- Limits on model iterations, calls per turn, time deadline and elapsed execution time.
- Tool results returned to the model under an untrusted_data wrapper, with prompt instructions to treat those results as untrusted.
- Recorded tools hash, tool parameters/results, model-visible responses where available, latency, usage, and errors.
- Durable investigation results, including the case version used, verdict, rationale, evidence, and evidence hash.
- [Human in the loop] Agent will not take action on the endpoint or user. [Guard] Lab will not accept agent key for writes. It is readonly. Only analyst can take action on host or user such as containment/release.
- Traces including thoughts help analysts inspect what the system did.
- [Planned in future] Agent will also propose actions, and rules for an analyst to take action.

AgenticSOC evaluates **the process as well as the final answer** for each case. 

The evaluation runner supports labelled cases, named model/configuration variants, k attempts per case/variant, persisted results, resumable batches, and independent grader versions.

Verdict - is being matched against labelled cases from lab.
Evidence grounding - is supported by case inputs, and tool results. tool result check if the evidence value presents in the result. It is a heuristic matching, not full deterministic check.
Human review - Analyst should judge the model rationale.

```shell
uv run --env-file .env python -m pipeline.soc_agent.evals.run --variants production,qwen2b,gemini-flash-lite --k 3 --note "baseline"
```

Ollama is optional and only needed for the local variants, and that variants are defined in [variant](pipeline/soc_agent/evals/variants.yaml).

## Observations from model evaluation using lab

- **Models inferring environment-specific facts from general knowledge.**
Larger models have general knowledge to answer "svc-maint", nightly.sh script, and maintenance schedule in the system. sometimes they derive conclusion from that instead of tool provided result. This needs more care to rectify. [not implemented - planned in future]
- **Smaller models need detailed instruction (playbook) than larger models.**
smaller models need more detailed instruction to identify relevant tools (arguments) and sequence an investigation. some cases Tools/Arguments with detailed instruction help small models to pick params well. This motivates a planned case classification → playbook retrieval step. [not implemented - planned in future]
- **Correct verdict is not enough.**
Models can reach right answer with wrong rationale. so rationale to be verified by analyst, evidence is linked to make that easy for analyst.

## Technology
Python, FastAPI, Pydantic, PostgreSQL (psycopg, psycopg_pool, SQLAlchemy, Alembic), HTTPX, uv, pytest, and Ruff. The project includes integrations for local Ollama models and Google GenAI.

Key source areas:
- pipeline/puller — ingestion and checkpoints
- pipeline/normalizer — alert normalization
- pipeline/cases — grouping, correlation, case persistence
- pipeline/soc_agent — agent runtime, tools, providers, evaluation
- api — case, investigation, and evaluation endpoints
- tests — tests

## Running locally: Run investigation

### Setting LAB

[SOCLab](https://github.com/dduraipandian/SOCLab) is used to trigger alerts. Simulated small fake company with five machines; an EDR sensor, a router to a fake internet, an attacker server, and three security vendors (EDR, identity provider, cloud) with real-looking APIs. 

```shell
git clone https://github.com/dduraipandian/SOCLab.git
# docker has to be installed and running
cd SOCLab
POSTGRES_PORT=5433 make up

# Trigger events
# user accessing /etc/shadow
make attack S=S2
# triggers benign nightly scheduled task accessing /etc/shadow
make attack S=S6
```

### Setting SOC Agent

```shell
git clone https://github.com/dduraipandian/AgenticSOC.git
cd AgenticSOC
uv sync

# update GEMINI_API_KEY with your gemini key
cp .env.example .env

# Configure .env and the application's lab/model settings for your environment.
make migrate

# runs each component in different shell, as it polls for data from endpoint, and postgres table
make run-puller
make run-normalizer
make run-cases

# runs soc portal backend to serve api
make run-portal-backend # backedn run in http://localhost:8121

make test

# Integration tests require a disposable PostgreSQL 18 test database configured via TEST_DATABASE_URL because the tests truncate tables. 
# So have unused DB for this.
TEST_DATABASE_URL="postgresql://soc:soc@localhost:5434/soc" make test-integration
```

### Setting up SOC ui

```shell
git clone https://github.com/dduraipandian/SOC-Portal-UI.git
cd SOC-Portal-UI
npm install
npm run dev # http://localhost:5173
```

1. Once lab, soc agent, and portal ui setup.
2. Trigger alerts in the given commands
3. Portal UI should show cases.
4. Click on the case, and run investigation.
