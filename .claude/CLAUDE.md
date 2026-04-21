# PyChaos — Claude Context

## Project Purpose
Modular workflow execution engine. Workflows orchestrate typed Tasks; every run is persisted to PostgreSQL. Supports the A2A (Agent-to-Agent) protocol so workflows/tasks are interoperable with other AI systems.

## Architecture Snapshot

```
src/pychaos/
├── db/
│   ├── models.py     # SQLAlchemy 2.x ORM (DeclarativeBase, Mapped)
│   ├── schema.py     # Pydantic v2 schemas + A2A protocol types
│   └── handler.py    # DatabaseHandler (pydantic-settings for creds)
├── workflow/
│   └── base.py       # BaseWorkflow — sequences tasks, logs to DB
└── tasks/
    ├── base.py       # BaseTask (abstract) — execute / init_context / process / verify_output / store_artifacts
    └── registry.py   # TaskRegistry + @register_task decorator
```

## Key Conventions

- **Task lifecycle**: `execute()` calls `init_context()` → `process()` → `verify_output()` in order.  Subclasses implement all three as `@abstractmethod`.
- **DB logging**: Pass a `DatabaseHandler` to `BaseWorkflow` or `BaseTask.__init__`; if omitted, the task runs without DB logging (useful for tests).
- **A2A types**: All inter-agent messages use types in `db/schema.py` (`A2ATask`, `A2AMessage`, `A2AArtifact`). These match the Google A2A spec camelCase field names.
- **Registry**: Decorate concrete task classes with `@register_task` to add them to `TaskRegistry`.
- **Status enum**: `TaskStatus` lives in `db/schema.py` and is shared by both the SQLAlchemy models (as a SAEnum column) and the Pydantic schemas.

## Environment Variables (`.env`)
```
PYCHAOS_DB_HOST=localhost
PYCHAOS_DB_PORT=5432
PYCHAOS_DB_NAME=pychaos
PYCHAOS_DB_USER=postgres
PYCHAOS_DB_PASSWORD=secret
PYCHAOS_DB_ECHO=false
```

## Running Tests
```bash
# Unit tests only (no DB required)
pytest -m unit

# All tests (requires local postgres)
pytest
```

## Adding a New Task
1. Subclass `BaseTask` in `src/pychaos/tasks/`
2. Set class attribute `task_type = "my_type"`
3. Implement `init_context`, `process`, `verify_output`
4. Decorate with `@register_task`
5. Import in `src/pychaos/tasks/__init__.py` to ensure registration
