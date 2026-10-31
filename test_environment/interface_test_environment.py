"""Sandbox test-harness doorway: FastAPI router for /api/testing/tool-workbench/* and
/api/sandbox/*."""

from fastapi import APIRouter, HTTPException

from test_environment.logic_test_environment import (
    ModelCallError,
    PromotionTestFailed,
    sandbox_env,
    tool_workbench,
)
from test_environment.schemas_test_environment import (
    SandboxCheckRequest,
    SandboxInstallRequest,
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
        return tool_workbench.test_with_llm(req.tool_code, req.model, req.user_prompt, req.timeout_seconds)
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
