"""
FastAPI application entry point.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Optional
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routes import router
from ..adapters.controller_simulator import RestSimulatorController
from ..adapters.sqlite_repository import SqliteRepository
from ..domain.actor import JunctionActor, JunctionRegistry
from ..domain.clock import RealClock
from ..domain.engine import JunctionEngine
from ..domain.recovery import recover_junction_from_storage
from ..domain.types import Direction, JunctionConfig, Phase

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def create_default_config() -> JunctionConfig:
    """Default Junction A configuration according to the specification."""
    return JunctionConfig(
        junction_id="A",
        directions=(Direction.NORTH, Direction.SOUTH, Direction.EAST, Direction.WEST),
        phases=(Phase.NORTH_SOUTH, Phase.EAST_WEST),
        green_duration=30.0,
        min_green=5.0,
        max_green=60.0,
        yellow_duration=5.0,
        all_red_duration=2.0,
        time_scale=2.0,  # 2x speed for demonstration
        manual_hold=10.0,
        emergency_stale_timeout=60.0,
        max_wait=90.0,
        grace_empty=2.0,
        ack_timeout=3.0,
        ack_retries=2,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown lifespan."""
    logger.info("Initializing Factory Traffic Management System...")

    db_path = os.environ.get("DB_PATH", "traffic.db")
    repository = SqliteRepository(db_path=db_path)
    registry = JunctionRegistry()

    # Configure default Junction A
    config = create_default_config()
    existing_cfg = await repository.load_junction_config("A")
    if not existing_cfg:
        await repository.save_junction_config(config)

    # Controller simulator (auto_ack enabled by default for simulated environment)
    auto_ack = os.environ.get("AUTO_ACK", "true").lower() in ("true", "1", "yes")
    controller = RestSimulatorController(junction_id="A", auto_ack=auto_ack, auto_ack_delay=0.1)

    engine = JunctionEngine(config=config, clock=RealClock())

    # Check for restart recovery
    waiting_vehicles = await repository.load_waiting_vehicles("A")
    if waiting_vehicles:
        logger.info("Existing state found in database. Performing restart recovery...")
        cmds = await recover_junction_from_storage(engine, repository)
        for cmd in cmds:
            await controller.send_command(cmd)
    else:
        # Fresh startup
        init_cmds = engine.initialize_green(Phase.NORTH_SOUTH)
        for cmd in init_cmds:
            await controller.send_command(cmd)

    # Create and start actor
    actor = JunctionActor(
        engine=engine,
        repository=repository,
        controller_port=controller,
        tick_interval=0.05,
    )
    registry.register(actor)
    actor.start()

    # Expose in app state
    app.state.repository = repository
    app.state.registry = registry
    app.state.controller = controller

    logger.info("Factory Traffic Management System ready on /api (Swagger docs at /docs)")
    yield

    logger.info("Shutting down Factory Traffic Management System...")
    await registry.stop_all()
    repository.close()
    logger.info("Shutdown complete.")


def create_app() -> FastAPI:
    """Application factory."""
    app = FastAPI(
        title="Factory Traffic Management System",
        description="Event-driven traffic signal control system for garment factory internal roads.",
        version="2.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # CORS configuration
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(router)

    @app.get("/", summary="Root health check")
    async def root():
        return {
            "name": "Factory Traffic Management System",
            "version": "2.0.0",
            "status": "RUNNING",
            "docs": "/docs",
        }

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.api.main:app", host="0.0.0.0", port=8000, reload=True)
