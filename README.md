# PyChaos

Modular workflow execution engine built on **SQLAlchemy 2**, **Pydantic v2**, **pydantic-ai**, and the **A2A protocol**.

Every workflow run and every task execution is persisted to PostgreSQL. Workflows are composable from typed, registry-backed Task classes. Inter-agent messages conform to Google's [A2A specification](https://google.github.io/A2A/).

---

## Architecture

```
src/pychaos/
├── db/
│   ├── models.py      SQLAlchemy 2 ORM  (WorkflowRun, TaskRun, Artifact)
│   ├── schema.py      Pydantic v2 schemas + A2A protocol types
│   └── handler.py     DatabaseHandler  (pydantic-settings for credentials)
├── workflow/
│   └── base.py        BaseWorkflow — sequences tasks, logs to DB
└── tasks/
    ├── base.py        BaseTask (abstract)
    └── registry.py    TaskRegistry + @register_task decorator
```

### Task lifecycle

```
execute()
  ├── init_context()   validate inputs, populate self.context
  ├── process()        core logic → self.output, call store_artifacts()
  └── verify_output()  assert expectations on self.output
```

### A2A types (`db/schema.py`)

| Type | Role |
|---|---|
| `TextPart / DataPart / FilePart` | Discriminated content parts |
| `A2AMessage` | User ↔ agent message |
| `A2ATask` | Full task object with status + artifacts |
| `A2AArtifact` | Output artifact in A2A format |

`TaskRunSchema.to_a2a_task()` and `ArtifactSchema.to_a2a()` convert internal DB schemas to A2A wire format.

---

## Quickstart

### 1. Install

```bash
pip install -e ".[dev]"
```

### 2. Configure the database

Set environment variables or create a `.env` file:

```
PYCHAOS_DB_HOST=localhost
PYCHAOS_DB_PORT=5432
PYCHAOS_DB_NAME=pychaos
PYCHAOS_DB_USER=postgres
PYCHAOS_DB_PASSWORD=secret
```

### 3. Initialise the schema

```python
from pychaos.db.handler import DatabaseHandler

db = DatabaseHandler.from_env()
db.initialize()          # creates tables on first run
db.health_check()        # True if connected
```

### 4. Write a task

```python
from pychaos.tasks.base import BaseTask
from pychaos.tasks.registry import register_task

@register_task
class DoubleTask(BaseTask):
    task_type = "double"

    def init_context(self) -> None:
        value = self.config.get("value")
        if value is None:
            raise ValueError("config.value is required")
        self.context["value"] = value

    def process(self) -> None:
        self.output["result"] = self.context["value"] * 2
        self.store_artifacts(
            name="doubled_value",
            artifact_type="metric",
            value={"result": self.output["result"]},
        )

    def verify_output(self) -> None:
        assert "result" in self.output
```

### 5. Build and run a workflow

```python
from pychaos.workflow.base import BaseWorkflow

wf = BaseWorkflow(name="my_pipeline", db_handler=db)
wf.add_task(DoubleTask("step1", wf.workflow_run_id, db_handler=db, config={"value": 21}))
results = wf.run()
# results["step1"] == {"result": 42}
```

### 6. Check the registry

```python
from pychaos.tasks.registry import TaskRegistry

print(TaskRegistry.list_types())   # ["double", ...]
cls = TaskRegistry.get("double")
```

---

## Running tests

```bash
# Unit tests — SQLite, no Postgres needed
pytest -m unit

# All tests (requires a local Postgres instance)
pytest

# Coverage report
pytest --cov=src/pychaos --cov-report=html
```

---

## End-to-end example

```bash
python examples/example_workflow.py
```

---

## Adding a new task — checklist

1. Create `src/pychaos/tasks/<name>.py` subclassing `BaseTask`
2. Set `task_type = "unique_string"` (not `"base"`)
3. Implement `init_context`, `process`, `verify_output`
4. Decorate with `@register_task`
5. Import in `src/pychaos/tasks/__init__.py` to trigger registration
6. Add tests in `tests/test_tasks/`
