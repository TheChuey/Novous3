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
        # os.makedirs creates directories recursively. exist_ok=True prevents errors if the directory exists.
        os.makedirs(path, exist_ok=True)
        result = {"path": path, "type": "directory", "created": True}

    except Exception as exc:
        raise RuntimeError(
            f"Could not create folder '{path}': {exc}"
        ) from exc

    return json.dumps(result, ensure_ascii=False, indent=2)

import tkinter as tk
import json
from typing import Dict, Any
from tkinter import messagebox


import json
import tkinter as tk
from typing import Dict



import json
import tkinter as tk
from typing import Dict


@tool
def ask_user_window(json_data: str) -> Dict[str, str]:
    """
    Ask the user multiple questions through a graphical questionnaire.

    Use this tool when you need to collect information or answers
    directly from the user.

    INPUT:
    json_data: JSON string containing question identifiers and text.

    Example:
    {
        "name": "What is your name?",
        "project": "What project are you working on?"
    }

    BEHAVIOR:
    - Opens a questionnaire window.
    - Displays one question at a time.
    - User types an answer and presses Enter or clicks Next.
    - Returns answers after all questions are completed.

    OUTPUT:
    Returns a dictionary containing question identifiers and answers.
    """

    # ==========================================
    # 1. VALIDATE INPUT
    # ==========================================

    # Models often send the questions as an object rather than a JSON string; accept both.
    try:
        questions_dict = json_data if isinstance(json_data, dict) else json.loads(json_data)

    except (json.JSONDecodeError, TypeError):
        return {}

    if not isinstance(questions_dict, dict) or not questions_dict:
        return {}

    if not all(isinstance(v, str) for v in questions_dict.values()):
        return {}

    question_keys = list(questions_dict.keys())
    question_texts = list(questions_dict.values())

    answers = {}
    current_question = 0

    # ==========================================
    # 2. CREATE WINDOW
    # ==========================================

    root = tk.Tk()
    root.title("Questionnaire")
    root.geometry("460x260")
    root.minsize(400, 220)
    root.configure(bg="#f5f6fa")

    # ==========================================
    # 3. NEXT QUESTION FUNCTION
    # ==========================================

    def on_next(event=None):

        nonlocal current_question

        if current_question >= len(question_keys):
            return "break"

        current_key = question_keys[current_question]

        answer = entry.get().strip()

        answers[current_key] = answer

        current_question += 1

        if current_question < len(question_texts):

            label.config(
                text=question_texts[current_question]
            )

            entry.delete(0, tk.END)
            entry.focus_set()

        else:
            root.destroy()

        return "break"

    # ==========================================
    # 4. MAIN FRAME
    # ==========================================

    frame = tk.Frame(
        root,
        bg="#f5f6fa",
        padx=30,
        pady=25
    )

    frame.pack(fill=tk.BOTH, expand=True)

    # ==========================================
    # 5. QUESTION LABEL
    # ==========================================

    label = tk.Label(
        frame,
        text=question_texts[current_question],
        font=("Helvetica", 13, "bold"),
        bg="#f5f6fa",
        fg="#2f3640",
        wraplength=400,
        justify="center"
    )

    label.pack(pady=(10, 20))

    # ==========================================
    # 6. ANSWER ENTRY
    # ==========================================

    entry = tk.Entry(
        frame,
        font=("Helvetica", 14),
        relief=tk.SOLID,
        bd=1
    )

    entry.pack(
        fill=tk.X,
        pady=(0, 20),
        ipady=5
    )

    # ==========================================
    # 7. NEXT BUTTON
    # ==========================================

    next_button = tk.Button(
        frame,
        text="Next",
        command=on_next,
        font=("Helvetica", 11, "bold"),
        bg="#40739e",
        fg="white",
        activebackground="#2f3640",
        activeforeground="white",
        relief=tk.FLAT,
        padx=20,
        pady=8,
        cursor="hand2"
    )

    next_button.pack()

    # ==========================================
    # 8. ENTER KEY BINDING
    # ==========================================

    root.bind("<Return>", on_next)

    root.after(100, entry.focus_set)

    # ==========================================
    # 9. START QUESTIONNAIRE
    # ==========================================

    root.mainloop()

    # ==========================================
    # 10. RETURN ANSWERS
    # ==========================================

    return answers

