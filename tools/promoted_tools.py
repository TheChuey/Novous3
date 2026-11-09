"""Permanent storage for custom tools promoted from the Prompt Testing Workbench.

Promoted tools are appended below the PROMOTION_MARKER by tools/logic_tools.py,
which performs AST validation, registry updates, and rollback on failure.
"""
from core_engine.logic_core_engine import tool

# PROMOTION_MARKER: tools appended below will be registered as custom tools

import json
import os


@tool
def create_folder(path: str) -> str:
    """Create workspace folders using relative paths.

    - Creates a folder, including any missing parent directories, at the specified path.
    - Uses exist_ok=True to avoid errors if the directory already exists.
    - Returns JSON describing the folder creation operation.
    """
    if not path or not path.strip():
        raise ValueError("A folder path is required.")

    try:
        os.makedirs(path, exist_ok=True)
        result = {"path": path, "type": "directory", "created": True}
    except Exception as exc:
        raise RuntimeError(f"Could not create folder '{path}': {exc}") from exc

    return json.dumps(result, ensure_ascii=False, indent=2)


import tkinter as tk

_ASK_USER_TIMEOUT_MS = 5 * 60 * 1000  # 5 minutes


@tool
def ask_user_window(questions: str) -> str:
    """Open a questionnaire window and return the user's answers as JSON. You MUST call this tool to collect information from the user.

    The questions parameter is a JSON object where each key is a short
    identifier and each value is the question text, for example:
    {"q1": "What error do you see?", "q2": "When did it start?"}

    Returns a JSON object: {"status": "success", "answers": {"q1": "...", "q2": "..."}}
    On error: {"status": "error", "message": "..."}
    """
    # Accept both a JSON string and an already-parsed dict (some models send objects).
    if isinstance(questions, dict):
        questions_dict = questions
    elif isinstance(questions, str):
        text = questions.strip()
        if not text:
            return json.dumps({"status": "error", "message": "The questions parameter is empty. Send a JSON object like {\"q1\": \"Your question?\"}."})
        try:
            questions_dict = json.loads(text)
        except json.JSONDecodeError as exc:
            return json.dumps({"status": "error", "message": f"Could not parse questions as JSON: {exc}. Send a JSON object like {{\"q1\": \"Your question?\"}}."})
    else:
        return json.dumps({"status": "error", "message": f"Expected a JSON string, got {type(questions).__name__}."})

    if not isinstance(questions_dict, dict) or not questions_dict:
        return json.dumps({"status": "error", "message": "The questions parameter must be a non-empty JSON object like {\"q1\": \"Your question?\"}."})

    bad_values = [k for k, v in questions_dict.items() if not isinstance(v, str)]
    if bad_values:
        return json.dumps({"status": "error", "message": f"Every value must be a question string. Non-string keys: {bad_values}"})

    question_keys = list(questions_dict.keys())
    question_texts = list(questions_dict.values())

    answers = {}
    current_question = [0]  # mutable container for the closure
    timed_out = [False]

    try:
        root = tk.Tk()
    except Exception as exc:
        return json.dumps({"status": "error", "message": f"Could not open the questionnaire window: {exc}"})

    root.title("Questionnaire")
    root.geometry("460x260")
    root.minsize(400, 220)
    root.configure(bg="#f5f6fa")

    def on_timeout():
        timed_out[0] = True
        root.destroy()

    timeout_id = root.after(_ASK_USER_TIMEOUT_MS, on_timeout)

    def on_next(event=None):
        idx = current_question[0]
        if idx >= len(question_keys):
            return "break"
        answers[question_keys[idx]] = entry.get().strip()
        current_question[0] += 1
        if current_question[0] < len(question_texts):
            label.config(text=question_texts[current_question[0]])
            entry.delete(0, tk.END)
            entry.focus_set()
        else:
            root.after_cancel(timeout_id)
            root.destroy()
        return "break"

    frame = tk.Frame(root, bg="#f5f6fa", padx=30, pady=25)
    frame.pack(fill=tk.BOTH, expand=True)

    label = tk.Label(frame, text=question_texts[0], font=("Helvetica", 13, "bold"),
                     bg="#f5f6fa", fg="#2f3640", wraplength=400, justify="center")
    label.pack(pady=(10, 20))

    entry = tk.Entry(frame, font=("Helvetica", 14), relief=tk.SOLID, bd=1)
    entry.pack(fill=tk.X, pady=(0, 20), ipady=5)

    next_button = tk.Button(frame, text="Next", command=on_next,
                            font=("Helvetica", 11, "bold"), bg="#40739e", fg="white",
                            activebackground="#2f3640", activeforeground="white",
                            relief=tk.FLAT, padx=20, pady=8, cursor="hand2")
    next_button.pack()

    root.bind("<Return>", on_next)
    root.after(100, entry.focus_set)

    try:
        root.mainloop()
    except Exception as exc:
        return json.dumps({"status": "error", "message": f"Questionnaire window error: {exc}"})

    if timed_out[0]:
        return json.dumps({"status": "error", "message": "The questionnaire timed out. The user did not answer in time."})

    if not answers:
        return json.dumps({"status": "error", "message": "The questionnaire was closed before any answers were given."})

    return json.dumps({"status": "success", "answers": answers}, ensure_ascii=False, indent=2)
