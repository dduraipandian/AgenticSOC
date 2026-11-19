import logging
from pathlib import Path
from typing import Any

from psycopg_pool import ConnectionPool

from common.db import make_pool
from config import config
from pipeline.cases.case_summary import case_summary
from pipeline.cases.db import CaseStore
from pipeline.soc_agent.agent import Agent
from pipeline.soc_agent.db import InvestigationRow, InvestigationStore
from pipeline.soc_agent.providers import PROVIDERS
from pipeline.soc_agent.providers.providers import Provider
from pipeline.soc_agent.tasks import get_task, select_tools
from pipeline.soc_agent.tools import TOOL_FACTORIES, TOOL_LIST
from pipeline.soc_agent.tools.case_tools import make_case_tools
from pipeline.soc_agent.tools.tools import Tool, tool_map
from vendors import VENDORS
from vendors.base import make_client

BASE_AGENT_PATH = Path(__file__).parent

log = logging.getLogger("agent")


class SystemPromptNotFound(Exception): ...


def run_agent(
    task_name,
    question: str,
    tools: dict[str, Tool],
    provider: Provider,
    task_config: dict | None = None,
    **agent_config,
):
    if not task_config:
        task_config = {}

    system_prompt = get_system_prompt(task_name)
    agent = Agent(tools, provider, **agent_config)
    response = agent.execute(system_prompt=system_prompt, question=question, **task_config)
    log.info("agent finished: %s %s", response.status, response.response)
    return response


def load_system_prompts(path: Path = BASE_AGENT_PATH) -> set:
    sp_path = path.joinpath("prompts")
    system_prompt_set = {f.stem for f in sp_path.glob("*.md")}
    return system_prompt_set


def get_system_prompt(name: str, path: Path = BASE_AGENT_PATH) -> str:
    sp_path = path.joinpath("prompts", f"{name}.md")
    return sp_path.read_text()


def build_tools(app_config, pool: ConnectionPool) -> dict:
    tool_list = list(TOOL_LIST)
    for source, vendor_config in app_config.adapters.items():
        actors = vendor_config.get("actors", {})
        if "agent" not in actors:
            continue

        agent_config: dict[str, Any] = actors["agent"]

        if agent_config.get("enabled") == False:
            continue

        vendor_api_cls = VENDORS[source]

        # use actor's own key
        client = make_client(vendor_config["url"], agent_config["api-key"], agent_config.get("timeout", 10))
        vendor_instance = vendor_api_cls(client, vendor_config)
        make_vendor_tools = TOOL_FACTORIES.get(source)

        if callable(make_vendor_tools):
            tool_list += make_vendor_tools(vendor_instance)
        else:
            log.warning(f"no tools are provided for {source}")

    tool_list += make_case_tools(pool, CaseStore())

    tools = tool_map(tool_list)
    return tools


def get_provider(name, app_config):
    model_provider_config = app_config.model_providers.get("providers", {})
    provider_config = dict(model_provider_config.get(name, {}))

    provider_cls = PROVIDERS[name]
    provider = provider_cls(provider_config)

    return provider


def agent_task(name: str, question: str, app_config: config.AppConfig, pool: ConnectionPool, **options):
    task = get_task(name)

    system_prompt_set = load_system_prompts()
    if name not in system_prompt_set:
        raise SystemPromptNotFound(f"system prompt {name} not found.")

    provider_name = task["provider_name"]
    agent_task_config = {"finish_tool": task["finish_tool"], **options}
    agent_config = app_config.model_providers.get("agent", {})

    provider = get_provider(provider_name, app_config)
    tools = select_tools(name, task, build_tools(app_config, pool))  # only this task's tools reach the model
    return run_agent(name, question, tools, provider, agent_task_config, **agent_config)


def investigate_case(
    case_id: int, app_config: config.AppConfig, pool: ConnectionPool, **options
) -> InvestigationRow:
    """One investigation of one case, using the caller's pool: the CLI below and the portal API both use it."""
    store = CaseStore()
    with pool.connection() as conn:
        case = store.get_case(conn, case_id)
        alerts = store.get_case_alerts(conn, case_id)

    if case is None:
        raise ValueError(f"case {case_id} not found")
    question = case_summary(case, alerts)
    log.info(question)

    response = agent_task("soc_investigation", question, app_config, pool, **options)

    # every run is saved, failed ones too; case["version"] is the version the agent was given.
    # The case's agent_status changes in the same transaction, so the portal never sees one without the other.
    row = InvestigationRow.from_agent_response(case_id, case["version"], response)
    with pool.connection() as conn, conn.transaction():
        investigation_id = InvestigationStore().save(conn, row)
        store.set_agent_status(conn, case_id, "done" if row.status == "success" else "failed")

    log.info("saved investigation %s for case %s: %s %s", investigation_id, case_id, row.status, row.verdict)
    return row


def soc_investigation(case_id: int) -> InvestigationRow:
    app_config = config.load_config()
    with make_pool(app_config.settings.DATABASE_URL) as pool:
        return investigate_case(case_id, app_config, pool)


if __name__ == "__main__":
    soc_investigation(1)
    # agent_task("case_summary", "Summarise case 1.")
