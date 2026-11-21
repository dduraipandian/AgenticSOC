"""Telling the lab's failures from the model's.

- `classify_failure`: a failed run is 'infra' when a model call or a vendor tool call failed on the connection
  (timeouts, refused or dropped connections, a model Ollama does not have, an overloaded API); otherwise 'agent'.
  Infra runs are kept but left out of pass rates, and the runner retries them once.
- `check_health` / `wait_until_healthy`: before each run, the database, the EDR and the variant's model provider
  must be up. The batch waits up to 5 minutes for them, then stops and says what is down.

A model call that times out counts as infra even when the model is just slow: the report shows the infra count
per variant, so a variant that keeps timing out is still visible.
"""

import time
from collections.abc import Callable

import httpx
import psycopg
from psycopg_pool import PoolTimeout

from pipeline.soc_agent.db import InvestigationRow
from pipeline.soc_agent.providers.ollama import Ollama
from pipeline.soc_agent.tasks import TaskOption

# Lowercased. Model call errors are repr(exc) ("ReadTimeout('timed out')"); tool errors are str(exc) ("timed out").
INFRA_MARKERS = (
    "timed out",
    "readtimeout",
    "connecttimeout",
    "connecterror",
    "connection refused",
    "all connection attempts failed",
    "remoteprotocolerror",
    "server disconnected",
    "pooltimeout",
    "backend_unavailable",
    "' not found\")",  # Ollama: model 'x' not found, i.e. not pulled
    "servererror(",  # Gemini 5xx
    "resource_exhausted",  # Gemini 429
    "-> 502",
    "-> 503",
    "-> 504",
)


def _is_infra(message: str | None) -> bool:
    text = (message or "").lower()
    return any(marker in text for marker in INFRA_MARKERS)


def classify_failure(row: InvestigationRow) -> str | None:
    """None for a successful run; 'infra' or 'agent' for a failed one."""
    if row.status != "failed":
        return None
    tool_errors = [e.get("error") for e in row.steps if e.get("type") == "tool" and e.get("error")]
    return "infra" if _is_infra(row.error) or any(map(_is_infra, tool_errors)) else "agent"


# --- health -----------------------------------------------------------------------------------------------------


class LabUnavailable(Exception):
    def __init__(self, problems: list[str]):
        super().__init__("the lab is not healthy: " + "; ".join(problems))
        self.problems = problems


def ollama_problems(http: httpx.Client, host: str, model: str) -> list[str]:
    try:
        http.get(f"{host}/api/version").raise_for_status()
        names = {m["name"] for m in http.get(f"{host}/api/tags").raise_for_status().json().get("models", [])}
    except httpx.HTTPError as exc:
        return [f"ollama at {host}: {exc!r}"]
    if model not in names and f"{model}:latest" not in names:
        return [f"ollama at {host} has no model {model!r} (ollama pull {model})"]
    return []


def check_health(task_option: TaskOption, app_config, pool, http: httpx.Client | None = None) -> list[str]:
    """What is down, as messages; empty when the run can start."""
    http = http or httpx.Client(timeout=5)
    problems: list[str] = []

    try:
        with pool.connection() as conn:
            conn.execute("SELECT 1")
    except (psycopg.OperationalError, PoolTimeout) as exc:
        problems.append(f"database: {exc!r}")

    for source, vendor_config in app_config.adapters.items():
        agent = vendor_config.get("actors", {}).get("agent")
        if not agent or agent.get("enabled") is False:
            continue
        try:
            http.head(f"{vendor_config['url']}/health").raise_for_status()
        except httpx.HTTPError as exc:
            problems.append(f"{source} at {vendor_config['url']}: {exc!r}")

    if isinstance(task_option.provider, Ollama):
        problems += ollama_problems(http, task_option.provider.host, task_option.model)
    elif type(task_option.provider).__name__ == "Gemini" and not app_config.settings.GEMINI_API_KEY:
        problems.append("gemini: GEMINI_API_KEY is not set")

    return problems


def wait_until_healthy(
    check: Callable[[], list[str]],
    timeout_s: float = 300,
    interval_s: float = 15,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Return once `check` finds nothing down; raise LabUnavailable with the last problems after `timeout_s`."""
    deadline = clock() + timeout_s
    while problems := check():
        if clock() + interval_s > deadline:
            raise LabUnavailable(problems)
        sleep(interval_s)
