import logging

from psycopg_pool import ConnectionPool

from pipeline.cases.case_summary import case_summary
from pipeline.cases.db import CaseStore
from pipeline.soc_agent.agent import Agent
from pipeline.soc_agent.db import InvestigationRow, InvestigationStore
from pipeline.soc_agent.domain_tasks.task import DomainTask
from pipeline.soc_agent.tasks import TaskOption

log = logging.getLogger("soc_investigation")


class CaseSummaryTask(DomainTask):
    name = "case_summary"

    def run(self, question, **options):
        return self.agent.execute(question, self.task_option)


class SocInvestigationTask(DomainTask):
    name = "soc_investigation"

    def __init__(self, task_option: TaskOption, agent: Agent):
        self.task_option = task_option
        self.agent = agent
        self.store = CaseStore()

    def build_case_question(self, conn, case_id: int) -> tuple[str, int, list[dict]]:
        case = self.store.get_case(conn, case_id)
        if case is None:
            raise ValueError(f"case {case_id} not found")
        alerts = self.store.get_case_alerts(conn, case_id)
        return case_summary(case, alerts), case["version"], alerts

    def run_investigation(self, case_id: int, pool: ConnectionPool) -> tuple[InvestigationRow, list[dict]]:
        with pool.connection() as conn:
            question, case_version, alerts = self.build_case_question(conn, case_id)

        log.info(question)

        response = self.agent.execute(question, self.task_option)
        # case["version"] is the version the agent was given
        return InvestigationRow.from_agent_response(case_id, case_version, response), alerts

    def investigate_case(self, case_id: int, pool: ConnectionPool) -> InvestigationRow:
        """One investigation of one case, using the caller's pool: the CLI below and the portal API both use it."""
        row, _ = self.run_investigation(case_id, pool)

        store = CaseStore()
        with pool.connection() as conn, conn.transaction():
            investigation_id = InvestigationStore().save(conn, row)
            store.set_agent_status(conn, case_id, "done" if row.status == "success" else "failed")

        log.info(
            "saved investigation %s for case %s: %s %s", investigation_id, case_id, row.status, row.verdict
        )
        return row

    def run(self, **params):
        case_id = params["case_id"]
        pool: ConnectionPool = params["pool"]
        return self.investigate_case(case_id=case_id, pool=pool)
