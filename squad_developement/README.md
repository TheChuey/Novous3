# Squad Development

Squad Development is a separate workspace for creating and testing rule-based squads.
Open **Squad Development** in the application. The Chat Agent remains available for
prompt-writing conversations.

**Prompt Starter** opens a separate editor containing the starter definition.
Its **Novous Generic Prompt** default includes the generated YAML frontmatter and prompt;
the sub-agent editor edits its generated prompt body while creation writes the
standard sub-agent YAML metadata and tools. **Add tools** opens the categorized,
filterable multi-select tool chooser used by the Editor Prompt Library. Selected
tools are written to the YAML frontmatter, and their descriptions and parameter
variables are added directly to the prompt. Parameter fields show each tool's
guidance beside the field.
Click a tool variable in the prompt to reopen the tool chooser. Click a
sub-agent placeholder to open its choice menu; `[SUB-AGENT ID]` offers the
sub-agent library or creates a new sub-agent from the Novous sub-agent default.
Other variables are filled in the side panel and appear in the prompt. Add
placeholders such as `[TASK]` to create fields automatically.
Type a value and use **Save choice** to reuse it across squads and app sessions;
choices are stored in `prompt_starter_choices.yaml` in the component folder.
**Copy prompt** copies the finished definition; **Use in left/right editor** replaces that
pane's prompt only after confirmation and leaves it unsaved until the agent is
saved. Unfilled placeholders are reported before applying. Choosing tools and
sub-agents outside the default agent tool list changes prompt text only; configure
handoffs separately.
The current Squad Development orchestration controls squad handoffs and does
not make direct `call_subagent` calls during a squad run.

Create a squad, then use the Markdown editor to customize the generic prompt
before creating an agent or sub-agent. The **Generic Prompt** button in the
editor's right panel reloads the template; replacing edited text requires confirmation.
**Save Generic Prompt** updates the shared `generic_prompt.md` template for future
agents without changing existing agent definitions.
Select an existing squad agent to edit its definition in the same editor and use
**Save agent**. **Generic Prompt** returns to the template, after confirming any
unsaved changes. The single Tools and Skills panel applies to the open document.
Pasting normalizes line endings. In Markdown documents, standalone uppercase
section names such as `ROLE` and labels such as `Purpose:` become headings. A flattened agent
prompt with recognizable inline YAML frontmatter, Markdown headings and lists
is split into lines. Text that does not match these rules is left unchanged;
the editor does not infer a structure for arbitrary prose. Markdown-escaped
delimiters and tool underscores in a recognized flattened agent are supported.
Review the result.
To format text already in a Markdown editor, click **Format Markdown** above the
editor. This formats the entire document but does not save it. For an existing
agent, its frontmatter must retain the selected agent's ID and use supported
tools; a failed save leaves the draft in the editor and displays the reason.
When pasting a
complete YAML mapping over the whole YAML document, the editor formats it. Invalid
YAML, comments, anchors, tags, directives, non-string or duplicate keys and partial YAML pastes
are left unchanged, with a message; review the result before saving.
As a precaution, YAML containing `#` inside a quoted value is also left unchanged.
Formatting is skipped if the document changes while it is being processed.
Each squad has one folder containing `squad.yaml`, agent Markdown files, sub-agent
YAML files, and `documentation.md` for ideas and skill notes. Edit each agent's
definition to change its prompt, model, and registered tools. Tool lists require
agent mode. The Tools panel shows the registered signature and documentation,
including return details where documented; it does not assume JSON. Adding a tool
inserts its documentation and enables it for the new or existing agent. When
editing the generic template, selected tools apply only to the next agent created;
saving the template saves its text, not tool selections.

The Skills panel saves reusable `skill-<id>.md` files inside the squad folder.
Inserting a skill copies its instructions into the prompt; it does not attach a
separate runtime skill, and later skill edits do not update previously inserted
copies. Lab notes are separate and are not injected into prompts.

In the flow map, select a starting agent and configure at most one success and one
failure route per agent. A step succeeds when the agent returns a nonempty answer
without engine or tool failures; exceptions, tool failures and exhausted tool
rounds count as failure. Each next step receives the user request and previous
step's outcome and result. A missing route ends the run. Callbacks are allowed,
but a run stops with an error if another step would exceed three steps.

Save changes before running. Runs use a snapshot of saved definitions, execute
through the existing orchestration worker, and may use configured tools that
modify files. The Lab is for notes only and does not provide draft execution.
Existing registered squads retain their existing sequential execution behavior.
