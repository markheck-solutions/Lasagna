"""Small desktop UI for pasted service IDs."""

from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from lasagna.live_batch import run_live_batch_from_text
from lasagna.snowflake.export import resolve_connection_name
from lasagna.workbook.paths import build_run_output_dir, default_output_root


class LasagnaApp:
    """Tkinter desktop app for live route workbook generation."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Lasagna")
        self.ids_text = tk.Text(root, height=14, width=64)
        self.connection = tk.StringVar(value=resolve_connection_name())
        self.output_root = tk.StringVar(value=str(default_output_root()))
        self.status = tk.StringVar(value="Ready")
        self.generate_button: ttk.Button | None = None
        self._build()

    def _build(self) -> None:
        frame = ttk.Frame(self.root, padding=12)
        frame.grid(row=0, column=0, sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(1, weight=1)

        ttk.Label(frame, text="IC/ICB IDs").grid(row=0, column=0, columnspan=3, sticky="w")
        self.ids_text.grid(row=1, column=0, columnspan=3, sticky="nsew")

        ttk.Label(frame, text="Snowflake connection").grid(row=2, column=0, sticky="w")
        ttk.Entry(frame, textvariable=self.connection).grid(
            row=2, column=1, columnspan=2, sticky="ew"
        )

        ttk.Label(frame, text="Output root").grid(row=3, column=0, sticky="w")
        ttk.Entry(frame, textvariable=self.output_root).grid(row=3, column=1, sticky="ew")
        ttk.Button(frame, text="Browse", command=self._select_output_root).grid(
            row=3,
            column=2,
            sticky="e",
        )

        self.generate_button = ttk.Button(frame, text="Generate", command=self._generate)
        self.generate_button.grid(row=4, column=0, sticky="w")
        ttk.Label(frame, textvariable=self.status).grid(row=4, column=1, columnspan=2, sticky="w")

    def _select_output_root(self) -> None:
        path = filedialog.askdirectory(initialdir=self.output_root.get())
        if path:
            self.output_root.set(path)

    def _generate(self) -> None:
        if self.generate_button is not None:
            self.generate_button.state(["disabled"])
        self.status.set("Generating...")
        thread = threading.Thread(target=self._run_generation, daemon=True)
        thread.start()

    def _run_generation(self) -> None:
        try:
            output_dir = run_live_batch_from_text(
                self.ids_text.get("1.0", "end"),
                output_dir=build_run_output_dir(Path(self.output_root.get())),
                connection=self.connection.get(),
            )
        except Exception as exc:
            self.root.after(0, self._generation_failed, str(exc))
            return
        self.root.after(0, self._generation_succeeded, str(output_dir))

    def _generation_failed(self, message: str) -> None:
        if self.generate_button is not None:
            self.generate_button.state(["!disabled"])
        self.status.set("Failed")
        messagebox.showerror("Lasagna", message)

    def _generation_succeeded(self, output_dir: str) -> None:
        if self.generate_button is not None:
            self.generate_button.state(["!disabled"])
        self.status.set(f"Output: {output_dir}")
        messagebox.showinfo("Lasagna", f"Workbooks created in:\n{output_dir}")


def main() -> None:
    root = tk.Tk()
    LasagnaApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
