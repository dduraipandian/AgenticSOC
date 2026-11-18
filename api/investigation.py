import logging
import time
from collections.abc import Callable, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated

import psycopg
from fastapi import BackgroundTasks, Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from psycopg_pool import ConnectionPool, PoolTimeout

from api import mapping
from api.models import (
    CaseDetail,
    CaseStatus,
    CaseSummary,
    Investigations,
    InvestigationStarted,
    ProposalStatus,
    QueueItem,
)
from common.db import make_pool
from pipeline.cases.db import CaseStore
from pipeline.soc_agent.db import InvestigationRow, InvestigationStore

log = logging.getLogger("api")

case_store = CaseStore()
inv_store = InvestigationStore()

# runs one investigation of a case with the given pool, and saves it (pipeline.soc_agent.main.investigate_case)
Investigator = Callable[[int, ConnectionPool], object]


class NotFound(Exception): ...


class AlreadyInvestigating(Exception): ...


def default_investigator(case_id: int, pool: ConnectionPool) -> None:
    from config.config import load_config
    from pipeline.soc_agent.main import investigate_case

    investigate_case(case_id, load_config(), pool)


def run_in_background(investigate: Investigator, pool: ConnectionPool, case_id: int) -> None:
    """Runs in the API process after the 202 is sent. A crash still ends as a failed run with its error,
    so the case is never left 'investigating' and the portal can say why it failed."""
    started, clock = datetime.now(UTC), time.monotonic()
    try:
        investigate(case_id, pool)
    except Exception as exc:
        log.exception("investigation of case %s crashed", case_id)
        with pool.connection() as conn, conn.transaction():
            case = case_store.get_case(conn, case_id)
            row = InvestigationRow(
                case_id=case_id,
                case_version=case["version"] if case else 0,
                status="failed",
                started_at=started,
                duration_ms=round((time.monotonic() - clock) * 1000),
                steps=[],
                history=[],
                error=f"the investigation crashed: {exc!r}",
            )
            inv_store.save(conn, row)
            case_store.set_agent_status(conn, case_id, "failed")


def create_app(
    pool: ConnectionPool | None = None, investigate: Investigator = default_investigator
) -> FastAPI:
    """With no pool given, one is opened at startup and closed at shutdown (tests pass their own)."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if pool is not None:
            app.state.pool = pool
            yield
            return

        from config.config import load_config

        with make_pool(load_config().settings.DATABASE_URL) as own_pool:
            app.state.pool = own_pool
            # runs started by a previous process died with it
            with own_pool.connection() as conn:
                if interrupted := case_store.reset_interrupted(conn):
                    log.warning("%s interrupted investigation(s) reset to pending", interrupted)
            yield

    app = FastAPI(title="agentic-soc portal api", lifespan=lifespan)

    # errors in the contract's shapes: Problem {error, message}, and FieldErrors for bad parameters
    @app.exception_handler(NotFound)
    def not_found(request: Request, exc: NotFound):
        return JSONResponse(status_code=404, content={"error": "not_found", "message": str(exc)})

    @app.exception_handler(AlreadyInvestigating)
    def already_investigating(request: Request, exc: AlreadyInvestigating):
        return JSONResponse(status_code=409, content={"error": "already_investigating", "message": str(exc)})

    @app.exception_handler(psycopg.OperationalError)
    @app.exception_handler(PoolTimeout)
    def unavailable(request: Request, exc: Exception):
        return JSONResponse(status_code=502, content={"error": "backend_unavailable", "message": str(exc)})

    @app.exception_handler(RequestValidationError)
    def bad_request(request: Request, exc: RequestValidationError):
        errors = [{"loc": ".".join(str(p) for p in e["loc"]), "msg": e["msg"]} for e in exc.errors()]
        return JSONResponse(status_code=400, content={"errors": errors})

    # plain `def` routes: psycopg is synchronous, and FastAPI runs these in a thread pool
    def connection(request: Request) -> Iterator[psycopg.Connection]:
        with request.app.state.pool.connection() as conn:
            yield conn

    Conn = Annotated[psycopg.Connection, Depends(connection)]
    Severity = Annotated[int | None, Query(ge=1, le=5, description="minimum severity")]
    Limit = Annotated[int, Query(ge=1, le=500)]

    @app.get("/queue", response_model=list[QueueItem])
    def queue(
        conn: Conn,
        status: CaseStatus | None = None,
        severity: Severity = None,
        entity: str | None = None,
        limit: Limit = 100,
    ):
        statuses = [status] if status else ["open", "merged"]  # the contract's default view
        cases = case_store.list_cases(conn, statuses, severity, entity, limit)
        latest = inv_store.latest_by_case(conn, [c["case_id"] for c in cases])
        return [mapping.queue_item(c, latest.get(c["case_id"])) for c in cases]

    @app.get("/cases", response_model=list[CaseSummary])
    def list_cases(
        conn: Conn,
        status: CaseStatus | None = None,
        severity: Severity = None,
        entity: str | None = None,
        limit: Limit = 100,
    ):
        cases = case_store.list_cases(conn, [status] if status else None, severity, entity, limit)
        return [mapping.case_summary(c) for c in cases]

    @app.get("/cases/{case_id}", response_model=CaseDetail)
    def get_case(case_id: int, conn: Conn):
        case = case_store.get_case(conn, case_id)
        if case is None:
            raise NotFound(f"case {case_id} not found")
        return mapping.case_detail(case, case_store.get_case_alerts(conn, case_id))

    @app.get("/investigations/{case_id}", response_model=Investigations)
    def get_investigations(case_id: int, conn: Conn):
        case = case_store.get_case(conn, case_id)
        if case is None:  # 404 means no such case; a case without runs is "pending", not missing
            raise NotFound(f"case {case_id} not found")

        runs = inv_store.get_by_case(conn, case_id)  # newest first
        views = [mapping.investigation_view(r) for r in runs if r["status"] == "success"]
        newest_failed = runs and runs[0]["status"] == "failed"
        return Investigations(
            case_id=case_id,
            agent_status=mapping.agent_status(case, runs[0] if runs else None),
            latest=views[0] if views else None,
            history=views[1:],
            last_failure=mapping.investigation_failure(runs[0]) if newest_failed else None,
        )

    @app.post("/investigations/{case_id}", status_code=202, response_model=InvestigationStarted)
    def start_investigation(case_id: int, conn: Conn, background: BackgroundTasks, request: Request):
        # claimed and committed before the 202, so a second click gets 409 instead of a second run
        with conn.transaction():
            claimed = case_store.claim_for_investigation(conn, case_id)
        if not claimed:
            if case_store.get_case(conn, case_id) is None:
                raise NotFound(f"case {case_id} not found")
            raise AlreadyInvestigating(f"case {case_id} is already being investigated")

        background.add_task(run_in_background, investigate, request.app.state.pool, case_id)
        return InvestigationStarted(case_id=case_id)

    @app.get("/proposals", response_model=list[dict])
    def list_proposals(case_id: int | None = None, status: ProposalStatus | None = None):
        # The agent proposes no response actions yet, so an empty list is the true answer. Replace with a real
        # query (newest first) once proposals are stored.
        return []

    return app


app = create_app()
