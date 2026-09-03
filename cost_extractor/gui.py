"""Tkinter desktop UI: file picker, rule checkboxes, preview, export."""

from __future__ import annotations

import io
import queue
import threading
import tkinter as tk
from dataclasses import replace
from datetime import date
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Optional

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD

    _HAS_DND = True
except Exception:  # noqa: BLE001 - native Tcl package can fail to load
    _HAS_DND = False

from PIL import Image

try:
    from PIL import ImageTk
except Exception:  # noqa: BLE001 - Pillow's Tk bridge needs a Tk-enabled build
    # The review pane still works without it; it just cannot show the crop,
    # which must never stop the app from starting.
    ImageTk = None

from cost_extractor import handwriting
from cost_extractor import date_rules
from cost_extractor.revisions import format_revision_timestamp, record_revision
from cost_extractor.money_parser import (
    CUSTOM_PATTERN_EXAMPLE_LABEL,
    CUSTOM_PATTERN_EXAMPLE_PATTERN,
    CUSTOM_PATTERN_HELP,
    MoneyFormatRule,
    build_custom_rule,
    default_rules,
    parse_amount,
)
from cost_extractor.ingestion import ARCHIVE_SUFFIX, SUPPORTED_SUFFIXES
from cost_extractor.pipeline import (
    DocumentResult,
    MatchRecord,
    PipelineResult,
    run_pipeline,
)
from cost_extractor.report import (
    REVIEW_FLAG,
    build_workbook,
    review_label,
    save_workbook,
)


# Distinguishes "not asked yet" from "asked, and the model said nothing",
# so a null reading isn't recomputed on every repaint.
_UNREAD = object()


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
        self._review_window: Optional[tk.Toplevel] = None
        self.review_index = 0
        self._review_photo = None
        self._second_opinions: dict[int, Optional[str]] = {}
        self.date_rules: list[date_rules.DateRule] = date_rules.default_rules()
        # Same id(match)-keyed cache shape as _second_opinions -- must be
        # invalidated whenever self.date_rules changes (Task 4).
        self._date_suggestions: dict[int, "Optional[date]"] = {}
        # Built once per run (in _run_worker, below) so a match can find
        # its owning document -- every existing flow iterates
        # "for doc in ... for m in doc.matches" and never needed the
        # reverse direction until now.
        self._match_documents: dict[int, DocumentResult] = {}
        self._custom_date_rule_count = 0
        self._spend_date_window: Optional[tk.Toplevel] = None
        self.spend_date_review_index = 0

        self._build_widgets()
        self._refresh_rule_checkboxes()
        self._refresh_run_button_state()
        self._refresh_date_rule_checkboxes()
        self._refresh_spend_date_button_state()

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

    # ---- reviewing what OCR guessed ----

    def reviewable_matches(self, pending_only: bool = False) -> list[MatchRecord]:
        """Every OCR-derived amount, worst-scoring first.

        Deliberately not filtered by confidence. Tesseract read $940.00 as
        $440.00 at 84% confidence, so a threshold cannot decide which
        guesses are safe to skip — only a person looking at the crop can.
        Sorting by score just means the most obviously doubtful get seen
        first if the user stops partway.
        """
        if self.last_result is None:
            return []
        matches = [
            m
            for doc in self.last_result.documents
            for m in doc.matches
            if m.provenance == "ocr" and not (pending_only and m.value_reviewed)
        ]
        return sorted(
            matches, key=lambda m: m.confidence if m.confidence is not None else 0.0
        )

    def can_review(self) -> bool:
        return bool(self.reviewable_matches())

    def apply_correction(
        self, match: MatchRecord, text: str, note: Optional[str] = None
    ) -> Optional[str]:
        """Records a human's reading. Returns an error message, or None.

        No default note: the Revised-From/To pair in the export already
        shows a change happened, so free text remains the richer channel
        for *why* rather than an auto-label.
        """
        value = parse_amount(text)
        if value is None:
            return "Enter an amount, e.g. 940.00 or ($200.00)"
        record_revision(match.value_revisions, value, note=note)
        self._after_review_change()
        return None

    def accept_reading(self, match: MatchRecord, note: Optional[str] = None) -> None:
        """Confirms OCR got it right. Still a decision, so still recorded.

        Defaults the note to "confirmed" when left blank (or whitespace):
        this is the one case where the value doesn't change, so the note
        is the only signal that a human deliberately reviewed it rather
        than it happening to match by coincidence.
        """
        cleaned = (note or "").strip() or None
        record_revision(match.value_revisions, match.value, note=cleaned or "confirmed")
        self._after_review_change()

    def second_opinion(self, match: MatchRecord) -> Optional[str]:
        """What the optional handwriting model makes of the same crop.

        Absent in every packaged build unless a model has been vendored
        deliberately. Never a value on its own — it is shown next to the
        primary reading so a person can weigh the two.
        """
        if not match.crop_png or not handwriting.is_available():
            return None
        cached = self._second_opinions.get(id(match), _UNREAD)
        if cached is not _UNREAD:
            return cached
        try:
            reading = handwriting.read_line(Image.open(io.BytesIO(match.crop_png)))
        except Exception:  # noqa: BLE001 - a second opinion is never worth a crash
            reading = None
        self._second_opinions[id(match)] = reading
        return reading

    def second_opinion_disagrees(self, match: MatchRecord) -> bool:
        """Whether the two engines read different numbers.

        Worth more than either confidence score: Tesseract read $940.00 as
        $440.00 at 82%, which no threshold catches, but a second engine
        reading it differently would have.
        """
        return handwriting.disagrees(match.raw_text, self.second_opinion(match))

    def use_second_opinion(
        self, match: MatchRecord, note: Optional[str] = None
    ) -> Optional[str]:
        """Adopts the model's reading, as a human decision.

        Routed through the same parsing and recording as a typed
        correction, so a suggestion can never slip into the totals without
        someone choosing it. The note always records that this value came
        from the model, even when the reviewer also adds their own note —
        a human's free-text note must never erase that provenance signal,
        since a value adopted from a model measured at 5/20 accuracy is a
        materially different kind of correction than one a reviewer typed
        independently, and the exported audit trail exists to show that.
        """
        reading = self.second_opinion(match)
        if not reading:
            return "No second reading available for this amount."
        cleaned = (note or "").strip() or None
        provenance = "adopted handwriting model's second opinion"
        combined_note = f"{cleaned} ({provenance})" if cleaned else provenance
        return self.apply_correction(match, reading, note=combined_note)

    def _document_for(self, match: MatchRecord) -> DocumentResult:
        return self._match_documents[id(match)]

    def suggest_spend_date(self, match: MatchRecord) -> Optional[date]:
        """The nearest date-like text found anywhere in this match's
        document, computed on demand and cached per match. Recomputed
        only when the cache is explicitly invalidated (rule changes --
        see Task 4), never on a timer or a document reload."""
        cached = self._date_suggestions.get(id(match), _UNREAD)
        if cached is not _UNREAD:
            return cached
        document = self._document_for(match)
        candidates = date_rules.find_dates(document.full_text, self.date_rules)
        nearest = date_rules.nearest_date(candidates, match.doc_offset)
        # nearest_date returns the closest DateMatch (or None), not a
        # bare date -- .value is None when the closest date-shaped text
        # nearby failed to parse, and that's still "no suggestion," not
        # license to fall back to a more distant candidate.
        suggestion = nearest.value if nearest is not None else None
        self._date_suggestions[id(match)] = suggestion
        return suggestion

    def confirm_spend_date(
        self, match: MatchRecord, date_str: str, note: Optional[str] = None
    ) -> Optional[str]:
        """Records a human-typed spend date. Parses date_str with the
        same date_rules the suggestion engine uses, so a typed correction
        is held to the same format understanding as a suggestion."""
        found = date_rules.find_dates(date_str, self.date_rules)
        parsed = next((m.value for m in found if m.value is not None), None)
        if parsed is None:
            return "Couldn't recognize that as a date"
        record_revision(match.spend_date_revisions, parsed, note=note)
        self._after_spend_date_change()
        return None

    def accept_date_suggestion(
        self, match: MatchRecord, note: Optional[str] = None
    ) -> Optional[str]:
        suggestion = self.suggest_spend_date(match)
        if suggestion is None:
            return "No date suggestion available for this document."
        cleaned = (note or "").strip() or None
        record_revision(match.spend_date_revisions, suggestion, note=cleaned or "confirmed")
        self._after_spend_date_change()
        return None

    def confirm_no_date(self, match: MatchRecord, note: Optional[str] = None) -> None:
        """The reviewer's explicit "no date applies" decision -- available
        regardless of whether a suggestion exists, distinct from
        accept_date_suggestion's automatic refusal when there is nothing
        to accept. Makes spend_date_reviewed=True with
        effective_spend_date=None a state the app produces on purpose."""
        record_revision(
            match.spend_date_revisions, None, note=note or "confirmed no associated date"
        )
        self._after_spend_date_change()

    def _after_spend_date_change(self) -> None:
        self._refresh_spend_date_widgets()

    def _refresh_spend_date_widgets(self) -> None:
        window = self._spend_date_window
        if window is None or not window.winfo_exists():
            return

        queue = self.spend_date_queue()
        match = self.current_spend_date_match()
        if match is None:
            self._spend_date_caption.config(text="Nothing left to confirm.")
            self._spend_date_position.config(text="")
            return

        self._spend_date_caption.config(
            text=(
                f"{match.display_name} — {match.location}\n"
                f"amount: {match.raw_text}  {self._spend_date_review_summary(match)}"
            )
        )
        self._spend_date_entry.delete(0, tk.END)
        if match.spend_date_reviewed and match.effective_spend_date is not None:
            self._spend_date_entry.insert(0, match.effective_spend_date.isoformat())
        self._spend_date_note_entry.delete(0, tk.END)
        self._spend_date_error.config(text="")
        self._spend_date_position.config(
            text=f"{self.spend_date_review_index + 1} of {len(queue)}"
        )
        self._refresh_spend_date_suggestion_widgets(match)

    def _spend_date_review_summary(self, match: MatchRecord) -> str:
        count = len(match.spend_date_revisions)
        if count == 0:
            return "(not yet reviewed)"
        latest = match.spend_date_revisions[-1]
        when = format_revision_timestamp(latest.at)
        note_suffix = f" ({latest.note})" if latest.note else ""
        value_text = latest.value.isoformat() if latest.value is not None else "no date"
        if count == 1:
            return f"— reviewed once: {value_text} at {when}{note_suffix}"
        return f"— reviewed {count}x, latest: {value_text} at {when}{note_suffix}"

    def _refresh_spend_date_suggestion_widgets(self, match: MatchRecord) -> None:
        suggestion = self.suggest_spend_date(match)
        if suggestion is None:
            self._spend_date_suggestion_label.config(
                text="No date suggestion found in this document."
            )
            self._spend_date_suggestion_button.pack_forget()
            return
        self._spend_date_suggestion_label.config(text=f"Suggested: {suggestion.isoformat()}")
        self._spend_date_suggestion_button.pack(side="left", padx=8)

    def add_date_rule(self, pattern_str: str, label: Optional[str] = None) -> Optional[str]:
        """Validates and adds a custom date rule. Returns an error
        message on failure (never raises), or None on success."""
        try:
            rule = date_rules.build_custom_rule(
                pattern_str, label or None, self._custom_date_rule_count
            )
        except ValueError as e:
            return str(e)
        self._custom_date_rule_count += 1
        self.date_rules.append(rule)
        self._date_suggestions.clear()
        self._refresh_date_rule_checkboxes()
        return None

    def remove_date_rule(self, rule_id: str) -> None:
        rule = next((r for r in self.date_rules if r.id == rule_id), None)
        if rule is None or rule.built_in:
            return  # built-ins are disableable but not deletable
        self.date_rules = [r for r in self.date_rules if r.id != rule_id]
        self._date_suggestions.clear()
        self._refresh_date_rule_checkboxes()

    def toggle_date_rule(self, rule_id: str, enabled: bool) -> None:
        for r in self.date_rules:
            if r.id == rule_id:
                r.enabled = enabled
        self._date_suggestions.clear()
        self._refresh_date_rule_checkboxes()

    def _refresh_date_rule_checkboxes(self) -> None:
        if not hasattr(self, "_date_rules_container"):
            return
        for child in self._date_rules_container.winfo_children():
            child.destroy()

        for rule in self.date_rules:
            row = ttk.Frame(self._date_rules_container)
            row.pack(fill="x")
            var = tk.BooleanVar(value=rule.enabled)
            cb = ttk.Checkbutton(
                row,
                text=rule.label,
                variable=var,
                command=lambda r=rule, v=var: self.toggle_date_rule(r.id, v.get()),
            )
            cb.pack(side="left")
            if not rule.built_in:
                ttk.Button(
                    row,
                    text="×",
                    width=2,
                    command=lambda rid=rule.id: self.remove_date_rule(rid),
                ).pack(side="left")

    def spend_date_queue(self) -> list[MatchRecord]:
        """Every match, not just OCR-derived ones -- a spend date applies
        regardless of how the amount was read."""
        if self.last_result is None:
            return []
        return [m for doc in self.last_result.documents for m in doc.matches]

    def can_confirm_spend_dates(self) -> bool:
        return bool(self.spend_date_queue())

    def current_spend_date_match(self) -> Optional[MatchRecord]:
        queue = self.spend_date_queue()
        if not queue:
            return None
        return queue[min(self.spend_date_review_index, len(queue) - 1)]

    def next_spend_date_review(self) -> None:
        queue = self.spend_date_queue()
        if queue:
            self.spend_date_review_index = min(
                self.spend_date_review_index + 1, len(queue) - 1
            )
        self._refresh_spend_date_widgets()

    def previous_spend_date_review(self) -> None:
        self.spend_date_review_index = max(0, self.spend_date_review_index - 1)
        self._refresh_spend_date_widgets()

    def current_review_match(self) -> Optional[MatchRecord]:
        queue = self.reviewable_matches()
        if not queue:
            return None
        return queue[min(self.review_index, len(queue) - 1)]

    def next_review(self) -> None:
        queue = self.reviewable_matches()
        if queue:
            self.review_index = min(self.review_index + 1, len(queue) - 1)
        self._refresh_review_widgets()

    def previous_review(self) -> None:
        self.review_index = max(0, self.review_index - 1)
        self._refresh_review_widgets()

    def _after_review_change(self) -> None:
        self._refresh_preview_widget()
        self._refresh_review_widgets()

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
        self._match_documents = {
            id(m): doc for doc in result.documents for m in doc.matches
        }
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
            wb = build_workbook(self.last_result, self.date_rules)
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

        date_rules_frame = ttk.LabelFrame(self.root, text="Date Formats")
        date_rules_frame.pack(fill="x", padx=8, pady=4)
        self._date_rules_container = ttk.Frame(date_rules_frame)
        self._date_rules_container.pack(fill="x")
        self._date_rule_error_label = ttk.Label(date_rules_frame, foreground="red", text="")
        self._date_rule_error_label.pack(fill="x")

        date_custom_frame = ttk.Frame(date_rules_frame)
        date_custom_frame.pack(fill="x", pady=(4, 0))
        ttk.Label(date_custom_frame, text="Custom pattern:").pack(side="left")
        self._date_pattern_entry = ttk.Entry(date_custom_frame)
        self._date_pattern_entry.pack(side="left", fill="x", expand=True, padx=4)
        ttk.Label(date_custom_frame, text="Label:").pack(side="left")
        self._date_label_entry = ttk.Entry(date_custom_frame, width=15)
        self._date_label_entry.pack(side="left", padx=4)
        ttk.Button(date_custom_frame, text="Add", command=self._on_add_date_rule).pack(
            side="left"
        )

        self._date_hint_label = ttk.Label(
            date_rules_frame,
            foreground="gray",
            text=(
                "Regex with required (?P<year>...), (?P<month>...), "
                "(?P<day>...) groups."
            ),
        )
        self._date_hint_label.pack(fill="x", pady=(0, 4))

        run_frame = ttk.Frame(self.root)
        run_frame.pack(fill="x", padx=8, pady=4)
        self._run_button = ttk.Button(
            run_frame, text="Run", command=self.start_run
        )
        self._run_button.pack(side="left")
        ttk.Button(run_frame, text="Cancel", command=self.request_cancel).pack(
            side="left", padx=4
        )
        self._review_button = ttk.Button(
            run_frame, text="Review Amounts...", command=self.open_review_window
        )
        self._review_button.pack(side="left", padx=4)
        self._review_button.state(["disabled"])
        self._spend_date_button = ttk.Button(
            run_frame, text="Confirm Spend Dates...", command=self.open_spend_date_window
        )
        self._spend_date_button.pack(side="left", padx=4)
        self._spend_date_button.state(["disabled"])
        ttk.Button(
            run_frame, text="Save Report...", command=self._on_export
        ).pack(side="left", padx=4)
        self._status_label = ttk.Label(run_frame, text="")
        self._status_label.pack(side="left", padx=8)

        preview_frame = ttk.LabelFrame(self.root, text="Preview")
        preview_frame.pack(fill="both", expand=True, padx=8, pady=4)
        columns = (
            "source",
            "location",
            "text",
            "rule",
            "value",
            "status",
            "read_as",
            "confidence",
            "review",
        )
        self._preview_tree = ttk.Treeview(
            preview_frame, columns=columns, show="headings", height=10
        )
        for col, label in zip(
            columns,
            [
                "Source File",
                "Location",
                "Matched Text",
                "Rule",
                "Value",
                "Status",
                "Read As",
                "Confidence",
                "Review",
            ],
        ):
            self._preview_tree.heading(col, text=label)
        self._preview_tree.pack(fill="both", expand=True)

    def open_review_window(self) -> Optional[tk.Toplevel]:
        """Opens (or raises) the pane for checking guessed amounts by eye."""
        existing = self._review_window
        if existing is not None and existing.winfo_exists():
            existing.lift()
            existing.focus_set()
            self._refresh_review_widgets()
            return existing

        if not self.can_review():
            return None

        window = tk.Toplevel(self.root)
        window.title("Review guessed amounts")
        self._review_window = window

        ttk.Label(
            window,
            text=(
                "These amounts were recognised from pixels, not read from a "
                "text layer.\nCheck each against the image before trusting "
                "the total."
            ),
            justify="left",
        ).pack(anchor="w", padx=10, pady=(10, 6))

        self._review_crop_label = ttk.Label(window, relief="sunken", anchor="center")
        self._review_crop_label.pack(padx=10, pady=6)

        self._review_caption = ttk.Label(window, justify="left")
        self._review_caption.pack(anchor="w", padx=10)

        second_row = ttk.Frame(window)
        second_row.pack(fill="x", padx=10, pady=(4, 0))
        self._second_opinion_label = ttk.Label(second_row, justify="left")
        self._second_opinion_label.pack(side="left")
        self._second_opinion_button = ttk.Button(
            second_row, text="Use this", command=self._on_use_second_opinion
        )

        entry_row = ttk.Frame(window)
        entry_row.pack(fill="x", padx=10, pady=6)
        ttk.Label(entry_row, text="Amount:").pack(side="left")
        self._review_entry = ttk.Entry(entry_row, width=18)
        self._review_entry.pack(side="left", padx=6)
        ttk.Button(entry_row, text="Save correction", command=self._on_save_correction).pack(
            side="left"
        )
        ttk.Button(entry_row, text="Looks right", command=self._on_accept_reading).pack(
            side="left", padx=4
        )

        note_row = ttk.Frame(window)
        note_row.pack(fill="x", padx=10)
        ttk.Label(note_row, text="Note (optional):").pack(side="left")
        self._review_note_entry = ttk.Entry(note_row, width=40)
        self._review_note_entry.pack(side="left", padx=6, fill="x", expand=True)

        self._review_error = ttk.Label(window, foreground="red")
        self._review_error.pack(anchor="w", padx=10)

        nav = ttk.Frame(window)
        nav.pack(fill="x", padx=10, pady=(4, 10))
        ttk.Button(nav, text="< Previous", command=self.previous_review).pack(side="left")
        ttk.Button(nav, text="Next >", command=self.next_review).pack(side="left", padx=4)
        self._review_position = ttk.Label(nav)
        self._review_position.pack(side="left", padx=10)

        self.review_index = 0
        self._refresh_review_widgets()
        return window

    def open_spend_date_window(self) -> Optional[tk.Toplevel]:
        """Opens (or raises) the pane for confirming each amount's spend
        date."""
        existing = self._spend_date_window
        if existing is not None and existing.winfo_exists():
            existing.lift()
            existing.focus_set()
            self._refresh_spend_date_widgets()
            return existing

        if not self.can_confirm_spend_dates():
            return None

        window = tk.Toplevel(self.root)
        window.title("Confirm spend dates")
        self._spend_date_window = window

        ttk.Label(
            window,
            text=(
                'Every amount needs a spend date, or a deliberate "no date '
                'applies."\nThe nearest date found in the document is '
                "suggested below."
            ),
            justify="left",
        ).pack(anchor="w", padx=10, pady=(10, 6))

        self._spend_date_caption = ttk.Label(window, justify="left")
        self._spend_date_caption.pack(anchor="w", padx=10)

        suggestion_row = ttk.Frame(window)
        suggestion_row.pack(fill="x", padx=10, pady=(4, 0))
        self._spend_date_suggestion_label = ttk.Label(suggestion_row, justify="left")
        self._spend_date_suggestion_label.pack(side="left")
        self._spend_date_suggestion_button = ttk.Button(
            suggestion_row, text="Use this", command=self._on_accept_date_suggestion
        )

        entry_row = ttk.Frame(window)
        entry_row.pack(fill="x", padx=10, pady=6)
        ttk.Label(entry_row, text="Date:").pack(side="left")
        self._spend_date_entry = ttk.Entry(entry_row, width=18)
        self._spend_date_entry.pack(side="left", padx=6)
        ttk.Button(entry_row, text="Save date", command=self._on_save_spend_date).pack(
            side="left"
        )
        ttk.Button(
            entry_row, text="No date applies", command=self._on_confirm_no_date
        ).pack(side="left", padx=4)

        note_row = ttk.Frame(window)
        note_row.pack(fill="x", padx=10)
        ttk.Label(note_row, text="Note (optional):").pack(side="left")
        self._spend_date_note_entry = ttk.Entry(note_row, width=40)
        self._spend_date_note_entry.pack(side="left", padx=6, fill="x", expand=True)

        self._spend_date_error = ttk.Label(window, foreground="red")
        self._spend_date_error.pack(anchor="w", padx=10)

        nav = ttk.Frame(window)
        nav.pack(fill="x", padx=10, pady=(4, 10))
        ttk.Button(nav, text="< Previous", command=self.previous_spend_date_review).pack(
            side="left"
        )
        ttk.Button(nav, text="Next >", command=self.next_spend_date_review).pack(
            side="left", padx=4
        )
        self._spend_date_position = ttk.Label(nav)
        self._spend_date_position.pack(side="left", padx=10)

        self.spend_date_review_index = 0
        self._refresh_spend_date_widgets()
        return window

    def _read_note_entry(self) -> Optional[str]:
        return self._review_note_entry.get().strip() or None

    def _on_save_correction(self) -> None:
        match = self.current_review_match()
        if match is None:
            return
        error = self.apply_correction(
            match, self._review_entry.get(), note=self._read_note_entry()
        )
        self._review_error.config(text=error or "")
        if error is None:
            self.next_review()

    def _on_accept_reading(self) -> None:
        match = self.current_review_match()
        if match is None:
            return
        self.accept_reading(match, note=self._read_note_entry())
        self._review_error.config(text="")
        self.next_review()

    def _revision_summary(self, match: MatchRecord) -> str:
        """The parenthetical after "read as $X" -- confidence, plus once
        reviewed, what changed and when. A bare Decimal is shown without a
        $ prefix, matching how effective_value is shown everywhere else in
        this app (the review entry field, the preview table)."""
        confidence = "unknown" if match.confidence is None else f"{match.confidence:.0f}%"
        count = len(match.value_revisions)
        if count == 0:
            return f"(confidence {confidence}, not yet reviewed)"

        latest = match.value_revisions[-1]
        when = format_revision_timestamp(latest.at)
        note_suffix = f" ({latest.note})" if latest.note else ""
        if count == 1:
            return (
                f"(confidence {confidence}) — reviewed once: "
                f"{latest.value} at {when}{note_suffix}"
            )
        return (
            f"(confidence {confidence}) — reviewed {count}x, latest: "
            f"{latest.value} at {when}{note_suffix}"
        )

    def _refresh_review_widgets(self) -> None:
        window = self._review_window
        if window is None or not window.winfo_exists():
            return

        queue = self.reviewable_matches()
        match = self.current_review_match()
        if match is None:
            self._review_caption.config(text="Nothing left to review.")
            self._review_position.config(text="")
            return

        self._show_crop(match)

        self._review_caption.config(
            text=(
                f"{match.display_name} — {match.location}\n"
                f"read as {match.raw_text}  {self._revision_summary(match)}"
            )
        )
        self._review_entry.delete(0, tk.END)
        self._review_entry.insert(0, str(match.effective_value))
        self._review_note_entry.delete(0, tk.END)
        self._review_position.config(
            text=f"{self.review_index + 1} of {len(queue)}"
        )
        self._refresh_second_opinion_widgets(match)

    def _refresh_second_opinion_widgets(self, match: MatchRecord) -> None:
        """Shows the handwriting model's reading, when one is installed."""
        reading = self.second_opinion(match)
        if not reading:
            self._second_opinion_label.config(text="")
            self._second_opinion_button.pack_forget()
            return

        if self.second_opinion_disagrees(match):
            # Two engines reading different numbers is the strongest signal
            # available that this one needs a careful look.
            text = f"Handwriting model reads: {reading}   (DISAGREES — check carefully)"
        else:
            text = f"Handwriting model reads: {reading}   (agrees)"
        self._second_opinion_label.config(text=text)
        self._second_opinion_button.pack(side="left", padx=8)

    def _on_use_second_opinion(self) -> None:
        match = self.current_review_match()
        if match is None:
            return
        error = self.use_second_opinion(match, note=self._read_note_entry())
        self._review_error.config(text=error or "")
        if error is None:
            self.next_review()

    def _read_spend_date_note_entry(self) -> Optional[str]:
        return self._spend_date_note_entry.get().strip() or None

    def _on_save_spend_date(self) -> None:
        match = self.current_spend_date_match()
        if match is None:
            return
        error = self.confirm_spend_date(
            match, self._spend_date_entry.get(), note=self._read_spend_date_note_entry()
        )
        self._spend_date_error.config(text=error or "")
        if error is None:
            self.next_spend_date_review()

    def _on_accept_date_suggestion(self) -> None:
        match = self.current_spend_date_match()
        if match is None:
            return
        error = self.accept_date_suggestion(match, note=self._read_spend_date_note_entry())
        self._spend_date_error.config(text=error or "")
        if error is None:
            self.next_spend_date_review()

    def _on_confirm_no_date(self) -> None:
        match = self.current_spend_date_match()
        if match is None:
            return
        self.confirm_no_date(match, note=self._read_spend_date_note_entry())
        self._spend_date_error.config(text="")
        self.next_spend_date_review()

    def _show_crop(self, match: MatchRecord) -> None:
        """Displays the pixels the amount was read from, if there are any."""
        if ImageTk is None or not match.crop_png:
            # A crop can legitimately be missing (render failure, or the
            # source could not be reopened). The amount still needs a
            # decision; it just has to be made without the picture.
            self._review_crop_label.config(
                image="", text="(no image available for this amount)"
            )
            self._review_photo = None
            return
        image = Image.open(io.BytesIO(match.crop_png))
        # Small crops are hard to judge at native size; never shrink one.
        scale = max(1, min(4, 220 // max(1, image.height)))
        if scale > 1:
            image = image.resize(
                (image.width * scale, image.height * scale), Image.LANCZOS
            )
        # Held on the instance because Tk keeps only a weak reference to a
        # PhotoImage; letting it be collected blanks the label.
        self._review_photo = ImageTk.PhotoImage(image)
        self._review_crop_label.config(image=self._review_photo, text="")

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

    def _refresh_review_button_state(self) -> None:
        """Enables review only when something was actually guessed."""
        if not hasattr(self, "_review_button"):
            return
        pending = len(self.reviewable_matches(pending_only=True))
        if self.can_review():
            self._review_button.state(["!disabled"])
            self._review_button.config(
                text=f"Review Amounts... ({pending})" if pending else "Review Amounts..."
            )
        else:
            self._review_button.state(["disabled"])
            self._review_button.config(text="Review Amounts...")

    def _refresh_spend_date_button_state(self) -> None:
        """Enables the button once a result exists -- every match needs a
        spend date, so unlike Review Amounts this never depends on
        whether anything was OCR-guessed."""
        if not hasattr(self, "_spend_date_button"):
            return
        if self.can_confirm_spend_dates():
            self._spend_date_button.state(["!disabled"])
        else:
            self._spend_date_button.state(["disabled"])

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
        self._refresh_review_button_state()
        self._refresh_spend_date_button_state()
        for doc in self.last_result.documents:
            self._insert_document_rows(doc)
        self._preview_tree.insert(
            "",
            tk.END,
            values=(
                "GRAND TOTAL",
                "",
                "",
                "",
                str(self.last_result.effective_grand_total),
                "",
                "",
                "",
                "",
            ),
        )
        # Only worth a line when some of that total actually rests on a
        # doubtful reading.
        review_total = self.last_result.review_total
        if review_total:
            self._preview_tree.insert(
                "",
                tk.END,
                values=(
                    "OF WHICH NEEDS REVIEW",
                    "",
                    "",
                    "",
                    str(review_total),
                    "",
                    "",
                    "",
                    REVIEW_FLAG,
                ),
            )

    def _insert_document_rows(self, doc: DocumentResult) -> None:
        if not doc.matches:
            self._preview_tree.insert(
                "",
                tk.END,
                values=(doc.display_name, "", "", "", "", doc.status.value, "", "", ""),
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
                    # A human's reading wins over the machine's.
                    str(m.effective_value),
                    doc.status.value,
                    m.provenance,
                    # Blank, not 0: nothing was guessed for a text-layer read.
                    "" if m.confidence is None else f"{m.confidence:.0f}%",
                    review_label(m) or "",
                ),
            )

    # ---- Tkinter event handlers ----

    def file_dialog_patterns(self) -> str:
        """The picker's filter, built from what ingestion actually accepts.

        Drag-and-drop never consulted this, so a hand-maintained list here
        could quietly disagree with the suffixes the pipeline supports.
        """
        suffixes = sorted(SUPPORTED_SUFFIXES) + [ARCHIVE_SUFFIX]
        return " ".join(f"*{s}" for s in suffixes)

    def _on_browse_files(self) -> None:
        paths = filedialog.askopenfilenames(
            filetypes=[("Supported", self.file_dialog_patterns())]
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

    def _on_add_date_rule(self) -> None:
        pattern = self._date_pattern_entry.get().strip()
        label = self._date_label_entry.get().strip()
        if not pattern:
            return
        error = self.add_date_rule(pattern, label)
        if error:
            self._date_rule_error_label.config(text=error)
        else:
            self._date_rule_error_label.config(text="")
            self._date_pattern_entry.delete(0, tk.END)
            self._date_label_entry.delete(0, tk.END)

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
