"""Read-only portal API: the endpoints of portal_ui/openapi.yaml the portal needs to show cases and the
agent's investigations. Run it where the portal's dev proxy expects it:

    uv run --env-file .env uvicorn api.investigation:app --port 8121 --reload
"""

from collections.abc import Iterator
from contextlib import asynccontextmanager
from typing import Annotated

import psycopg
from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from psycopg_pool import ConnectionPool, PoolTimeout

from api import mapping
from api.models import CaseDetail, CaseStatus, CaseSummary, Investigations, ProposalStatus, QueueItem
from common.db import make_pool
from pipeline.cases.db import CaseStore
from pipeline.soc_agent.db import InvestigationStore

case_store = CaseStore()
inv_store = InvestigationStore()


class NotFound(Exception): ...


def create_app(pool: ConnectionPool | None = None) -> FastAPI:
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
            yield

    app = FastAPI(title="agentic-soc portal api", lifespan=lifespan)

    # errors in the contract's shapes: Problem {error, message}, and FieldErrors for bad parameters
    @app.exception_handler(NotFound)
    def not_found(request: Request, exc: NotFound):
        return JSONResponse(status_code=404, content={"error": "not_found", "message": str(exc)})

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
        return Investigations(
            case_id=case_id,
            agent_status=mapping.agent_status(case, runs[0] if runs else None),
            latest=views[0] if views else None,
            history=views[1:],
        )

    @app.get("/proposals", response_model=list[dict])
    def list_proposals(case_id: int | None = None, status: ProposalStatus | None = None):
        # The agent proposes no response actions yet, so an empty list is the true answer. Replace with a real
        # query (newest first) once proposals are stored.
        return []

    return app


app = create_app()
