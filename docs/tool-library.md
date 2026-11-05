# Tools

Agents call the tools in `tools/`, `core_engine/` and the promoted module. This page covers the
Tool Library and the Tool Workbench, where tools are edited, tested and added.

## Where things are

- `tools/promoted_tools.py`: custom tools added from the Workbench. Each one is a public
  top-level function decorated with `@tool`, after the `# PROMOTION_MARKER` line.
- `tools/data/tool_prompts.json`: the wording the LLM sees for each tool, saved from the
  Tool Library (`{"tool_name": "one-line wording"}`).
- `tools/logic_tools.py` (`ToolRegistryService`): add, update, remove and read tools.
- `tools/interface_tools.py`: the `/api/tools/*` routes.

## Writing a tool

- The **docstring** describes how the tool works, what it expects as input and what it
  returns. You read it in the code and the Workbench.
- The **prompt wording** is the one line an agent sees in its function schema and in its
  `## Available Tools` section. Write it as a use instruction, for example:
  "Ask the user questions in a questionnaire window when you need their background, goals or
  constraints before you can give advice; returns their answers."
- When no wording is saved, the agent sees the first line of the docstring.
- The function name is the tool name. Use short snake_case names. Models misspell long
  camelCase names, and a misspelled name cannot be called.

## Tool Library (Tools tab → Tool Workbench → Registered Tool Library)

- **Edit** opens a tool.
  - Custom tools: change the code and click **Save code**. The function must keep its name.
    Syntax errors, a missing function, or code that fails to load are refused, and the file
    is restored.
  - Built-in tools: the code is read-only, but the prompt wording can be changed.
- **Prompt description**: save the wording for the tool. Saving rewrites the
  `## Available Tools` section of every agent and sub-agent that uses the tool, so the new
  wording reaches them. Clear the box and save to go back to the docstring.
  - Wording is one default per tool. Different wording per model is not built yet.
  - Do not hand-edit the `## Available Tools` section of an agent. It is generated, and
    saving the tool's wording replaces it.
- **Remove** deletes a custom tool from `promoted_tools.py`.

## Tool Workbench test (Stage 2: Ask LLM to Use Function)

- Tick one or more installed models. Models without tool support are shown but cannot be
  ticked. Each ticked model is asked in turn, and the results appear in a table.
- Click a row to see that model's reply, the arguments it sent and how the call was made
  (`native` for a structured tool call, `text_json` for a call written as JSON text).
- **Check the call only** asks for the call and checks its arguments, without running the
  tool. Use it for tools that open a window, such as the questionnaire.
- The test uses the same schema an agent sends: the saved wording, and every parameter as a
  string. A call that passes here is the call an agent would make.
- Results to look for:
  - `CALL_VALID`: the model made a valid call (dry run).
  - `SUCCESS`: the call ran and returned a result.
  - `ERR_NO_TOOL_CALL`: the model replied in text and made no call.
  - `ERR_UNEXPECTED_TOOL_CALL`: it called another name. A near-miss name gets a hint.
  - `ERR_INVALID_TOOL_ARGUMENTS`: the arguments do not match the signature.
  - `UNSUPPORTED`: the model cannot use tools.
- Every model is asked once. One run can pass by chance, so repeat the test on a model you
  plan to rely on.

## Stage 3 and 4: direct Python test and promotion

- **Direct Python Test** runs the code locally with the arguments you enter. It is not
  isolated from the filesystem or network. GUI tools open their windows here.
- **Add Passed Tool to Library** promotes the tool after its direct test passes. It does not
  yet require a passing LLM test.

## Checking agents against models

Each agent's tools are checked against each installed model's tool limit. See
[`modelProfiles/README.md`](../modelProfiles/README.md#tool-limits-and-tool-checks) for the
`pinned_tools` setting and the warnings.
