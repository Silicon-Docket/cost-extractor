"""Tkinter desktop UI: file picker, rule checkboxes, preview, export."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from dataclasses import replace
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Optional

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD

    _HAS_DND = True
except Exception:  # noqa: BLE001 - native Tcl package can fail to load
    _HAS_DND = False

from cost_extractor.money_parser import (
    CUSTOM_PATTERN_EXAMPLE_LABEL,
    CUSTOM_PATTERN_EXAMPLE_PATTERN,
    CUSTOM_PATTERN_HELP,
    MoneyFormatRule,
    build_custom_rule,
    default_rules,
)
from cost_extractor.pipeline import DocumentResult, PipelineResult, run_pipeline
from cost_extractor.report import build_workbook, save_workbook


def create_root() -> tk.Tk:
    """Creates the Tk root, preferring drag-and-drop support but never
    failing startup if the native Tcl package can't load."""
    if _HAS_DND:
        try:
            return TkinterDnD.Tk()
        except Exception:  # noqa: BLE001
            pass
    return tk.Tk()


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.rules: list[MoneyFormatRule] = default_rules()
        self.selected_paths: list[Path] = []
        self.cancel_flag = threading.Event()
        self.last_result: Optional[PipelineResult] = None
        self.status_message: str = ""
        self._progress_queue: "queue.Queue[str]" = queue.Queue()
        self._custom_rule_count = 0
        self._worker_thread: Optional[threading.Thread] = None
        self._help_window: Optional[tk.Toplevel] = None

        self._build_widgets()
        self._refresh_rule_checkboxes()
        self._refresh_run_button_state()

    # ---- state mutation: pure-ish logic, testable without a running mainloop ----

    def add_paths(self, paths: list[Path]) -> None:
        for p in paths:
            if p not in self.selected_paths:
                self.selected_paths.append(p)
        self._refresh_file_list_widget()
        self._refresh_run_button_state()

    def remove_path(self, path: Path) -> None:
        self.selected_paths = [p for p in self.selected_paths if p != path]
        self._refresh_file_list_widget()
        self._refresh_run_button_state()

    def toggle_rule(self, rule_id: str, enabled: bool) -> None:
        for r in self.rules:
            if r.id == rule_id:
                r.enabled = enabled
        self._refresh_run_button_state()

    def add_custom_pattern(self, pattern_str: str, label: str) -> Optional[str]:
        """Validates and adds a custom rule. Returns an error message on
        failure (never raises), or None on success."""
        try:
            rule = build_custom_rule(
                pattern_str, label or None, self._custom_rule_count
            )
        except ValueError as e:
            return str(e)
        self._custom_rule_count += 1
        self.rules.append(rule)
        self._refresh_rule_checkboxes()
        self._refresh_run_button_state()
        return None

    def remove_custom_rule(self, rule_id: str) -> None:
        self.rules = [r for r in self.rules if r.id != rule_id]
        self._refresh_rule_checkboxes()
        self._refresh_run_button_state()

    def can_run(self) -> bool:
        return bool(self.selected_paths) and any(r.enabled for r in self.rules)

    def _snapshot_active_rules(self) -> list[MoneyFormatRule]:
        """Independent copies of the currently-enabled rules.

        `run_pipeline` re-checks `rule.enabled` on every text segment it
        processes. Without this snapshot, toggling a checkbox mid-run
        would change which rules apply to files processed after the
        toggle, silently making one run inconsistent with itself. Copies
        are decoupled from the live GUI rule objects, so later checkbox
        changes cannot affect an in-flight run.
        """
        return [replace(r, enabled=True) for r in self.rules if r.enabled]

    # ---- run / cancel / export ----

    def start_run(self) -> None:
        if not self.can_run() or self._worker_thread is not None:
            return
        self.cancel_flag = threading.Event()
        active_rules = self._snapshot_active_rules()
        paths = list(self.selected_paths)
        self._set_status("Running...")
        self._worker_thread = threading.Thread(
            target=self._run_worker, args=(paths, active_rules), daemon=True
        )
        self._worker_thread.start()
        self.root.after(100, self._poll_progress)

    def _run_worker(
        self, paths: list[Path], active_rules: list[MoneyFormatRule]
    ) -> None:
        result = run_pipeline(
            paths,
            active_rules,
            ocr_enabled=True,
            progress_cb=lambda name: self._progress_queue.put(f"progress:{name}"),
            cancel_flag=self.cancel_flag,
        )
        self.last_result = result
        self._progress_queue.put("done")

    def request_cancel(self) -> None:
        self.cancel_flag.set()

    def _poll_progress(self) -> None:
        try:
            while True:
                msg = self._progress_queue.get_nowait()
                if msg == "done":
                    self._worker_thread = None
                    self._set_status("Done")
                    self._refresh_preview_widget()
                    return
                elif msg.startswith("progress:"):
                    self._set_status(f"Processing: {msg[len('progress:'):]}")
        except queue.Empty:
            pass
        if self._worker_thread is not None:
            self.root.after(100, self._poll_progress)

    def export_report(self, path: Path) -> Optional[str]:
        """Writes the last pipeline result to `path`. Returns an error
        message on failure, None on success."""
        if self.last_result is None:
            return "Nothing to export yet — run first."
        try:
            wb = build_workbook(self.last_result)
            save_workbook(wb, path)
        except Exception as e:  # noqa: BLE001 - e.g. file open elsewhere
            return str(e)
        return None

    def _set_status(self, message: str) -> None:
        self.status_message = message
        if hasattr(self, "_status_label"):
            self._status_label.config(text=message)

    # ---- Tkinter widget wiring ----

    def _build_widgets(self) -> None:
        self.root.title("Cost Extractor")

        file_frame = ttk.LabelFrame(self.root, text="Files")
        file_frame.pack(fill="x", padx=8, pady=4)

        self._file_listbox = tk.Listbox(file_frame, height=5)
        self._file_listbox.pack(side="left", fill="x", expand=True, padx=4, pady=4)

        btns = ttk.Frame(file_frame)
        btns.pack(side="left", padx=4)
        ttk.Button(btns, text="Add Files...", command=self._on_browse_files).pack(
            fill="x"
        )
        ttk.Button(btns, text="Add Folder...", command=self._on_browse_folder).pack(
            fill="x"
        )
        ttk.Button(btns, text="Remove Selected", command=self._on_remove_selected).pack(
            fill="x"
        )

        if _HAS_DND and hasattr(self.root, "drop_target_register"):
            try:
                self._file_listbox.drop_target_register(DND_FILES)
                self._file_listbox.dnd_bind("<<Drop>>", self._on_drop)
            except Exception:  # noqa: BLE001
                pass

        rules_frame = ttk.LabelFrame(self.root, text="Money Formats")
        rules_frame.pack(fill="x", padx=8, pady=4)
        self._rules_container = ttk.Frame(rules_frame)
        self._rules_container.pack(fill="x")
        self._rule_error_label = ttk.Label(rules_frame, foreground="red", text="")
        self._rule_error_label.pack(fill="x")

        custom_frame = ttk.Frame(rules_frame)
        custom_frame.pack(fill="x", pady=(4, 0))
        ttk.Label(custom_frame, text="Custom pattern:").pack(side="left")
        self._custom_pattern_entry = ttk.Entry(custom_frame)
        self._custom_pattern_entry.pack(side="left", fill="x", expand=True, padx=4)
        ttk.Label(custom_frame, text="Label:").pack(side="left")
        self._custom_label_entry = ttk.Entry(custom_frame, width=15)
        self._custom_label_entry.pack(side="left", padx=4)
        ttk.Button(custom_frame, text="Add", command=self._on_add_custom_pattern).pack(
            side="left"
        )
        ttk.Button(
            custom_frame, text="?", width=2, command=self.show_custom_pattern_help
        ).pack(side="left", padx=(4, 0))

        # Always-visible one-liner; the full guide is behind the "?" button.
        self._custom_hint_label = ttk.Label(
            rules_frame,
            foreground="gray",
            text=(
                "Regex with a required (?P<amount>...) group. Optional: "
                "(?P<mult>...) for K/M/B, (?P<sign>...) for negatives. "
                "Click ? for details and an example."
            ),
        )
        self._custom_hint_label.pack(fill="x", pady=(0, 4))
        rules_frame.bind(
            "<Configure>",
            lambda e: self._custom_hint_label.config(wraplength=max(e.width - 16, 100)),
        )

        run_frame = ttk.Frame(self.root)
        run_frame.pack(fill="x", padx=8, pady=4)
        self._run_button = ttk.Button(
            run_frame, text="Run", command=self.start_run
        )
        self._run_button.pack(side="left")
        ttk.Button(run_frame, text="Cancel", command=self.request_cancel).pack(
            side="left", padx=4
        )
        ttk.Button(
            run_frame, text="Save Report...", command=self._on_export
        ).pack(side="left", padx=4)
        self._status_label = ttk.Label(run_frame, text="")
        self._status_label.pack(side="left", padx=8)

        preview_frame = ttk.LabelFrame(self.root, text="Preview")
        preview_frame.pack(fill="both", expand=True, padx=8, pady=4)
        columns = ("source", "location", "text", "rule", "value", "status")
        self._preview_tree = ttk.Treeview(
            preview_frame, columns=columns, show="headings", height=10
        )
        for col, label in zip(
            columns,
            ["Source File", "Location", "Matched Text", "Rule", "Value", "Status"],
        ):
            self._preview_tree.heading(col, text=label)
        self._preview_tree.pack(fill="both", expand=True)

    def show_custom_pattern_help(self) -> tk.Toplevel:
        """Opens (or raises, if already open) the custom-pattern guide."""
        existing = self._help_window
        if existing is not None and existing.winfo_exists():
            existing.lift()
            existing.focus_set()
            return existing

        window = tk.Toplevel(self.root)
        window.title("Custom money-format patterns")
        window.transient(self.root)

        body = ttk.Frame(window)
        body.pack(fill="both", expand=True)
        line_count = CUSTOM_PATTERN_HELP.count("\n")  # text ends with a newline
        text = tk.Text(
            body, wrap="word", width=78, height=min(line_count, 45), padx=8, pady=8
        )
        scrollbar = ttk.Scrollbar(body, orient="vertical", command=text.yview)
        text.config(yscrollcommand=scrollbar.set)
        text.insert("1.0", CUSTOM_PATTERN_HELP)
        text.config(state="disabled")
        scrollbar.pack(side="right", fill="y")
        text.pack(side="left", fill="both", expand=True)

        buttons = ttk.Frame(window)
        buttons.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Button(
            buttons, text="Use example", command=self.use_example_pattern
        ).pack(side="left")
        ttk.Button(buttons, text="Close", command=window.destroy).pack(side="right")

        self._help_window = window
        return window

    def use_example_pattern(self) -> None:
        """Fills the custom-pattern fields with the documented example so the
        user can just click Add (or edit it first)."""
        self._custom_pattern_entry.delete(0, tk.END)
        self._custom_pattern_entry.insert(0, CUSTOM_PATTERN_EXAMPLE_PATTERN)
        self._custom_label_entry.delete(0, tk.END)
        self._custom_label_entry.insert(0, CUSTOM_PATTERN_EXAMPLE_LABEL)
        self._custom_pattern_entry.focus_set()

    def _refresh_rule_checkboxes(self) -> None:
        for child in self._rules_container.winfo_children():
            child.destroy()

        for rule in self.rules:
            row = ttk.Frame(self._rules_container)
            row.pack(fill="x")
            var = tk.BooleanVar(value=rule.enabled)
            cb = ttk.Checkbutton(
                row,
                text=rule.label,
                variable=var,
                command=lambda r=rule, v=var: self.toggle_rule(r.id, v.get()),
            )
            cb.pack(side="left")
            if not rule.built_in:
                ttk.Button(
                    row,
                    text="×",
                    width=2,
                    command=lambda rid=rule.id: self.remove_custom_rule(rid),
                ).pack(side="left")

    def _refresh_run_button_state(self) -> None:
        if self.can_run():
            self._run_button.state(["!disabled"])
            self._rule_error_label.config(text="")
        else:
            self._run_button.state(["disabled"])
            if not any(r.enabled for r in self.rules):
                self._rule_error_label.config(
                    text="Select at least one format to search for"
                )
            else:
                self._rule_error_label.config(text="")

    def _refresh_file_list_widget(self) -> None:
        self._file_listbox.delete(0, tk.END)
        for p in self.selected_paths:
            self._file_listbox.insert(tk.END, str(p))

    def _refresh_preview_widget(self) -> None:
        for row in self._preview_tree.get_children():
            self._preview_tree.delete(row)
        if self.last_result is None:
            return
        for doc in self.last_result.documents:
            self._insert_document_rows(doc)
        self._preview_tree.insert(
            "",
            tk.END,
            values=("GRAND TOTAL", "", "", "", str(self.last_result.grand_total), ""),
        )

    def _insert_document_rows(self, doc: DocumentResult) -> None:
        if not doc.matches:
            self._preview_tree.insert(
                "",
                tk.END,
                values=(doc.display_name, "", "", "", "", doc.status.value),
            )
            return
        for m in doc.matches:
            self._preview_tree.insert(
                "",
                tk.END,
                values=(
                    m.display_name,
                    m.location,
                    m.raw_text,
                    m.rule_id,
                    str(m.value),
                    doc.status.value,
                ),
            )

    # ---- Tkinter event handlers ----

    def _on_browse_files(self) -> None:
        paths = filedialog.askopenfilenames(
            filetypes=[("Supported", "*.docx *.pdf *.zip")]
        )
        self.add_paths([Path(p) for p in paths])

    def _on_browse_folder(self) -> None:
        folder = filedialog.askdirectory()
        if folder:
            self.add_paths([Path(folder)])

    def _on_remove_selected(self) -> None:
        for i in self._file_listbox.curselection():
            self.remove_path(self.selected_paths[i])

    def _on_drop(self, event) -> None:
        paths = self.root.tk.splitlist(event.data)
        self.add_paths([Path(p) for p in paths])

    def _on_add_custom_pattern(self) -> None:
        pattern = self._custom_pattern_entry.get().strip()
        label = self._custom_label_entry.get().strip()
        if not pattern:
            return
        error = self.add_custom_pattern(pattern, label)
        if error:
            self._rule_error_label.config(text=error)
        else:
            self._custom_pattern_entry.delete(0, tk.END)
            self._custom_label_entry.delete(0, tk.END)

    def _on_export(self) -> None:
        if self.last_result is None:
            messagebox.showinfo("Cost Extractor", "Run first, then save the report.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".xlsx", filetypes=[("Excel", "*.xlsx")]
        )
        if not path:
            return
        error = self.export_report(Path(path))
        if error:
            messagebox.showerror("Cost Extractor", f"Could not save report: {error}")
