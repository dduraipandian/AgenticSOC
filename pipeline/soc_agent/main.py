import logging
from pathlib import Path
from typing import Any

from psycopg_pool import ConnectionPool

from common.db import make_pool
from config import config
from pipeline.cases.db import CaseStore
from pipeline.soc_agent.agent import Agent
from pipeline.soc_agent.domain_tasks import DOMAIN_TASKS
from pipeline.soc_agent.tasks import UnknownTask, load_domain_tasks
from pipeline.soc_agent.tools import TOOL_FACTORIES, TOOL_LIST
from pipeline.soc_agent.tools.case_tools import make_case_tools
from pipeline.soc_agent.tools.tools import tool_map
from vendors import VENDORS
from vendors.base import make_client

BASE_AGENT_PATH = Path(__file__).parent

log = logging.getLogger("agent")


class SystemPromptNotFound(Exception): ...


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


def agent_task(name, domain_tasks, pool, /, **kwargs):
    if name not in domain_tasks:
        raise UnknownTask(f"{name} not found in the task config.")

    if name not in DOMAIN_TASKS:
        raise UnknownTask(f"{name} is not implemented or configured.")

    domain_task_config = domain_tasks[name]
    agent = Agent(**domain_task_config.agent_options)

    domain_task_cls = DOMAIN_TASKS[name]
    domain_task_instance = domain_task_cls(domain_task_config, agent)
    return domain_task_instance.run(**kwargs, pool=pool)


def investigate_case(case_id: int, pool: ConnectionPool, app_config: config.AppConfig | None = None):
    app_config = app_config or config.load_config()
    domain_tasks = load_domain_tasks(build_tools(app_config, pool))
    return agent_task("soc_investigation", domain_tasks, pool, case_id=case_id)


if __name__ == "__main__":
    app_config = config.load_config()
    case_id = 3
    with make_pool(app_config.settings.DATABASE_URL) as pool:
        investigate_case(case_id, pool, app_config)
