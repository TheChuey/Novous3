"""Sandbox test-harness doorway: FastAPI router for /api/testing/tool-workbench/*,
/api/testing/suites/* and /api/sandbox/*."""

from fastapi import APIRouter, HTTPException

from test_environment.logic_test_environment import (
    ModelCallError,
    PromotionTestFailed,
    sandbox_env,
    tool_workbench,
)
from test_environment.logic_test_suites import test_suite_store
from test_environment.schemas_test_environment import (
    SandboxCheckRequest,
    SandboxInstallRequest,
    SuiteRunRequest,
    SuiteSaveRequest,
    ToolWorkbenchGenerateRequest,
    ToolWorkbenchLLMRequest,
    ToolWorkbenchRequest,
)
from tools.logic_tools import ToolConflictError

router = APIRouter()


@router.post("/api/testing/tool-workbench/execute")
def api_execute_tool_test(req: ToolWorkbenchRequest):
    return tool_workbench.run_python_tool(req.tool_code, req.function_input, timeout=req.timeout_seconds)


@router.post("/api/testing/tool-workbench/test-harness")
def api_test_harness(req: ToolWorkbenchRequest):
    try:
        return {"harness": tool_workbench.build_test_harness(req.tool_code)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/testing/tool-workbench/test-with-llm")
def api_test_tool_with_llm(req: ToolWorkbenchLLMRequest):
    try:
        return tool_workbench.test_with_llm(
            req.tool_code, req.model, req.user_prompt, req.timeout_seconds,
            execute=req.execute, description=req.description,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ModelCallError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/api/testing/tool-workbench/generate-function")
def api_generate_tool_function(req: ToolWorkbenchGenerateRequest):
    try:
        generated = tool_workbench.generate_python_function(req.description, req.model)
    except ValueError as exc:
        return {"status": "ERROR", "error_code": "ERR_INVALID_GENERATED_FUNCTION",
                "message": str(exc), "code": None, "function_name": None}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Function generation failed: {exc}") from exc
    return {"status": "SUCCESS", "error_code": None, **generated}


@router.post("/api/testing/tool-workbench/promote")
def api_promote_tool(req: ToolWorkbenchRequest):
    try:
        return tool_workbench.promote(req.tool_code, req.function_input, req.timeout_seconds)
    except PromotionTestFailed as exc:
        raise HTTPException(
            status_code=400,
            detail={"message": str(exc), "test_result": exc.test_result},
        ) from exc
    except ToolConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/sandbox/packages")
def api_sandbox_packages():
    return {"exists": sandbox_env.exists(), "packages": sandbox_env.list_packages()}


@router.post("/api/sandbox/check")
def api_sandbox_check(req: SandboxCheckRequest):
    return {"missing": sandbox_env.missing_modules(req.source, req.modules)}


@router.post("/api/sandbox/install")
def api_sandbox_install(req: SandboxInstallRequest):
    try:
        return sandbox_env.install(req.packages)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/sandbox/reset")
def api_sandbox_reset():
    return sandbox_env.reset()


@router.get("/api/testing/tool-workbench/tools/{tool_name}")
def api_get_workbench_tool(tool_name: str):
    try:
        return tool_workbench.get_custom_tool_source(tool_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/api/testing/tool-workbench/tools/{tool_name}")
def api_delete_workbench_tool(tool_name: str):
    try:
        return tool_workbench.delete_custom_tool(tool_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not delete tool: {exc}") from exc


# --- Saved test suites -----------------------------------------------------------

def _suite_call(fn, *args):
    try:
        return fn(*args)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api/testing/suites")
def api_list_test_suites(category: str | None = None):
    return {"suites": _suite_call(test_suite_store.list_suites, category)}


@router.post("/api/testing/suites")
def api_save_test_suite(req: SuiteSaveRequest):
    return _suite_call(test_suite_store.save_suite, req.model_dump())


@router.get("/api/testing/suites/{category}/{slug}")
def api_get_test_suite(category: str, slug: str):
    return _suite_call(test_suite_store.get_suite, category, slug)


@router.delete("/api/testing/suites/{category}/{slug}")
def api_delete_test_suite(category: str, slug: str):
    _suite_call(test_suite_store.delete_suite, category, slug)
    return {"status": "deleted"}


@router.get("/api/testing/suites/{category}/{slug}/results")
def api_get_test_suite_results(category: str, slug: str):
    return _suite_call(test_suite_store.get_results, category, slug)


@router.post("/api/testing/suites/{category}/{slug}/results")
def api_append_test_suite_result(category: str, slug: str, req: SuiteRunRequest):
    return _suite_call(test_suite_store.append_result, category, slug, req.model_dump())
