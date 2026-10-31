"""CodeBuilder pillar package.

Layers (dependencies point downward only):

    interface_codebuilder.py   HTTP adapter (FastAPI router, SSE framing)
    logic_codebuilder.py       use-cases, sandbox script files, shared terminal
    execution.py               engine: isolated child-process runs, stuck handling
    schemas_codebuilder.py     contracts (pydantic models)
"""
