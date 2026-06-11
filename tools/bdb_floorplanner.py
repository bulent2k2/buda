#!/usr/bin/env python3
"""
BUDA floorplanner prototype.

This is intentionally a thin Tk/Matplotlib frontend over the C++
FloorplannerEngine.  It is meant for quick manual placement experiments and
HBundle-flow script export, not as the final polished editor.
"""

from __future__ import annotations

import os
import sys
import tkinter as tk
from tkinter import filedialog, simpledialog, ttk

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure
import matplotlib.patches as mpatches

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import floorplanner_commands as fpc


class BdbFloorplanner:
    def __init__(self, root):
        self.root = root
        self.root.title("BUDA Floorplanner Prototype")
        self.root.geometry("1360x820")

        self.state = fpc.new_state()
        self._patch_to_name = {}
        self._drag = None
        self._status = tk.StringVar(value="Open or create a BDB to begin.")

        self._bdb_var = tk.StringVar()
        self._die_w = tk.DoubleVar(value=2000.0)
        self._die_h = tk.DoubleVar(value=1200.0)
        self._grid = tk.DoubleVar(value=10.0)
        self._depth = tk.IntVar(value=0)
        self._sel_var = tk.StringVar(value="")
        self._issue_var = tk.StringVar(value="")

        self._build_ui()

    def _build_ui(self):
        top = ttk.Frame(self.root, padding=(6, 4))
        top.pack(side=tk.TOP, fill=tk.X)
        ttk.Label(top, text="BDB:").grid(row=0, column=0, sticky="w")
        ttk.Entry(top, textvariable=self._bdb_var, width=64).grid(
            row=0, column=1, sticky="ew", padx=4)
        ttk.Button(top, text="Open", command=self._open_bdb).grid(row=0, column=2, padx=2)
        ttk.Button(top, text="New", command=self._new_bdb).grid(row=0, column=3, padx=2)
        ttk.Button(top, text="Import Verilog", command=self._import_verilog).grid(row=0, column=4, padx=2)
        ttk.Button(top, text="Write", command=self._write_bdb).grid(row=0, column=5, padx=2)
        ttk.Button(top, text="Export Flow", command=self._export_flow).grid(row=0, column=6, padx=2)
        ttk.Button(top, text="Run Flow", command=self._run_flow).grid(row=0, column=7, padx=2)
        top.columnconfigure(1, weight=1)

        main = ttk.Frame(self.root)
        main.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=4, pady=2)

        left = ttk.Frame(main, width=260)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 4))
        left.pack_propagate(False)

        setup = ttk.LabelFrame(left, text="Canvas", padding=6)
        setup.pack(fill=tk.X, pady=(0, 4))
        self._spin(setup, "Die W", self._die_w, 0)
        self._spin(setup, "Die H", self._die_h, 1)
        self._spin(setup, "Grid", self._grid, 2)
        self._spin(setup, "Depth", self._depth, 3)
        ttk.Button(setup, text="Apply", command=self._apply_canvas).grid(
            row=4, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        self._depth.trace_add("write", lambda *_: self._on_depth_change())

        blocks = ttk.LabelFrame(left, text="Blocks", padding=6)
        blocks.pack(fill=tk.BOTH, expand=True, pady=(0, 4))
        filter_f = ttk.Frame(blocks)
        filter_f.pack(fill=tk.X)
        ttk.Button(filter_f, text="Add", command=self._add_block).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(filter_f, text="Align Bottom", command=self._align_bottom).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))
        self._block_list = tk.Listbox(blocks, selectmode=tk.EXTENDED, exportselection=False)
        self._block_list.pack(fill=tk.BOTH, expand=True, pady=(4, 0))
        self._block_list.bind("<<ListboxSelect>>", lambda _: self._on_list_select())

        props = ttk.LabelFrame(left, text="Selection", padding=6)
        props.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(props, textvariable=self._sel_var, anchor="w").pack(fill=tk.X)

        checks = ttk.LabelFrame(left, text="Validation", padding=6)
        checks.pack(fill=tk.X)
        ttk.Button(checks, text="Validate", command=self._validate).pack(fill=tk.X)
        ttk.Label(checks, textvariable=self._issue_var, anchor="w", justify=tk.LEFT).pack(fill=tk.X, pady=(4, 0))

        canvas_f = ttk.Frame(main)
        canvas_f.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._fig = Figure(figsize=(9, 7), facecolor="#f3f4f6")
        self._ax = self._fig.add_subplot(111)
        self._canvas = FigureCanvasTkAgg(self._fig, master=canvas_f)
        NavigationToolbar2Tk(self._canvas, canvas_f).update()
        self._canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        self._canvas.mpl_connect("button_press_event", self._on_press)
        self._canvas.mpl_connect("motion_notify_event", self._on_motion)
        self._canvas.mpl_connect("button_release_event", self._on_release)

        ttk.Label(self.root, textvariable=self._status, relief=tk.SUNKEN,
                  anchor="w", padding=(6, 2)).pack(side=tk.BOTTOM, fill=tk.X)
        self._draw()

    @staticmethod
    def _spin(parent, label, var, row):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=1)
        ttk.Spinbox(parent, textvariable=var, from_=1, to=1_000_000, increment=10, width=10).grid(
            row=row, column=1, sticky="ew", pady=1)
        parent.columnconfigure(1, weight=1)

    def _open_bdb(self):
        path = filedialog.askopenfilename(filetypes=[("BDB", "*.bdb"), ("All", "*")])
        if not path:
            return
        self.state = fpc.load_bdb(path)
        self._bdb_var.set(path)
        self._sync_canvas_vars()
        self._refresh_list()
        self._draw()
        self._status.set(f"Loaded {len(self.state.block_names)} placed block(s).")

    def _new_bdb(self):
        path = filedialog.asksaveasfilename(defaultextension=".bdb",
                                            filetypes=[("BDB", "*.bdb"), ("All", "*")])
        if not path:
            return
        self.state = fpc.create_bdb(path, self._die_w.get(), self._die_h.get(), self._grid.get())
        self._bdb_var.set(path)
        self._refresh_list()
        self._draw()
        self._status.set("Created new floorplan BDB.")

    def _import_verilog(self):
        v_path = filedialog.askopenfilename(
            filetypes=[("Verilog", "*.v *.sv"), ("All", "*")])
        if not v_path:
            return
        default_bdb = os.path.splitext(v_path)[0] + ".bdb"
        bdb_path = filedialog.asksaveasfilename(
            initialfile=os.path.basename(default_bdb),
            initialdir=os.path.dirname(default_bdb),
            defaultextension=".bdb",
            filetypes=[("BDB", "*.bdb"), ("All", "*")])
        if not bdb_path:
            return
        self.state = fpc.import_verilog(
            v_path, bdb_path, self._die_w.get(), self._die_h.get(), self._grid.get())
        self._bdb_var.set(bdb_path)
        self._sync_canvas_vars()
        self._refresh_list()
        self._draw()
        suffix = ""
        if self.state.unplaced_names:
            suffix = f" Seeded {len(self.state.unplaced_names)} placeholder block(s)."
        self._status.set(
            f"Imported Verilog hierarchy from {os.path.basename(v_path)}.{suffix}")

    def _apply_canvas(self):
        fpc.set_die(self.state, self._die_w.get(), self._die_h.get())
        fpc.set_grid(self.state, self._grid.get())
        self._draw()
        self._status.set("Canvas settings applied.")

    def _add_block(self):
        name = simpledialog.askstring("Add Block", "Instance path:", parent=self.root)
        if not name:
            return
        x = simpledialog.askfloat("Add Block", "X origin:", initialvalue=100.0, parent=self.root)
        y = simpledialog.askfloat("Add Block", "Y origin:", initialvalue=100.0, parent=self.root)
        w = simpledialog.askfloat("Add Block", "Width:", initialvalue=200.0, parent=self.root)
        h = simpledialog.askfloat("Add Block", "Height:", initialvalue=160.0, parent=self.root)
        if None in (x, y, w, h):
            return
        fpc.add_block(self.state, name, x, y, w, h)
        self._refresh_list()
        self._select_name(name)
        self._draw()

    def _align_bottom(self):
        names = self._selected_list_names()
        if len(names) < 2:
            self._status.set("Select at least two blocks to align.")
            return
        fpc.align_bottom(self.state, names)
        self._draw()
        self._status.set(f"Aligned {len(names)} block(s) to bottom edge.")

    def _validate(self):
        issues = fpc.validate(self.state)
        if not issues:
            self._issue_var.set("No issues.")
            self._status.set("Floorplan validation passed.")
            return
        lines = []
        for issue in issues[:5]:
            if issue.kind == "OVERLAP":
                lines.append(f"OVERLAP: {issue.block_a} / {issue.block_b}")
            elif issue.kind == "OUTSIDE_DIE":
                lines.append(f"OUTSIDE: {issue.block_a}")
            else:
                lines.append(f"{issue.kind}: {issue.message}")
        if len(issues) > 5:
            lines.append(f"... {len(issues) - 5} more")
        self._issue_var.set("\n".join(lines))
        self._status.set(f"Validation found {len(issues)} issue(s).")

    def _write_bdb(self):
        if not self.state.bdb_path:
            path = self._bdb_var.get().strip()
            if not path:
                self._new_bdb()
                return
            self.state.bdb_path = path
        fpc.write_bdb(self.state)
        self._status.set(f"Placements written to {self.state.bdb_path}.")

    def _export_flow(self):
        path = filedialog.asksaveasfilename(defaultextension=".buda",
                                            filetypes=[("BUDA script", "*.buda"), ("All", "*")])
        if not path:
            return
        fpc.export_hbundle_script(self.state, path, depth=max(1, int(self._depth.get())))
        self._status.set(f"Exported HBundle flow script to {path}.")

    def _run_flow(self):
        if not self.state.bdb_path:
            self._status.set("Create, open, or import a BDB before running flow.")
            return
        self._status.set("Running HBundle flow...")
        self.root.update_idletasks()
        result = fpc.run_hbundle_flow(self.state, depth=max(1, int(self._depth.get())))
        tail = (result.stdout or result.stderr).strip().splitlines()[-1:] or [""]
        if result.returncode == 0:
            self._status.set(f"HBundle flow completed. {tail[0]}")
        else:
            self._status.set(f"HBundle flow failed ({result.returncode}). {tail[0]}")

    def _sync_canvas_vars(self):
        if self.state.engine.die_w() > 0:
            self._die_w.set(self.state.engine.die_w())
        if self.state.engine.die_h() > 0:
            self._die_h.set(self.state.engine.die_h())
        self._grid.set(self.state.engine.grid())

    def _refresh_list(self):
        self._block_list.delete(0, tk.END)
        for name in self._visible_names():
            self._block_list.insert(tk.END, name)

    def _visible_names(self):
        return self.state.names_at_depth(int(self._depth.get()))

    def _selected_list_names(self):
        return [self._block_list.get(i) for i in self._block_list.curselection()]

    def _on_list_select(self):
        names = self._selected_list_names()
        self.state.selected = names[-1] if names else None
        self._draw()

    def _select_name(self, name):
        self.state.selected = name
        self._block_list.selection_clear(0, tk.END)
        visible = self._visible_names()
        if name in visible:
            idx = visible.index(name)
            self._block_list.selection_set(idx)
            self._block_list.see(idx)

    def _on_depth_change(self):
        self.state.selected = None
        self._refresh_list()
        self._draw()

    def _draw(self):
        ax = self._ax
        ax.clear()
        self._patch_to_name.clear()

        dw, dh = self.state.engine.die_w(), self.state.engine.die_h()
        if dw > 0 and dh > 0:
            ax.add_patch(mpatches.Rectangle(
                (0, 0), dw, dh, facecolor="#f8fafc", edgecolor="#6b7280",
                linewidth=1.2, zorder=0))

        for block in self.state.blocks_at_depth(int(self._depth.get())):
            selected = block.name == self.state.selected
            patch = mpatches.Rectangle(
                (block.x1, block.y1), block.x2 - block.x1, block.y2 - block.y1,
                facecolor="#8ecae6" if selected else "#d9e8f5",
                edgecolor="#0f172a" if selected else "#475569",
                linewidth=2.0 if selected else 0.9,
                alpha=0.92, picker=True, zorder=2)
            ax.add_patch(patch)
            self._patch_to_name[patch] = block.name
            ax.text(block.x1 + 4, block.y1 + 4, block.name,
                    fontsize=7.5, color="#0f172a", va="bottom", clip_on=True, zorder=3)

        self._update_selection_label()
        if dw > 0 and dh > 0:
            margin = max(dw, dh) * 0.04
            ax.set_xlim(-margin, dw + margin)
            ax.set_ylim(-margin, dh + margin)
        elif self._visible_names():
            xs = []
            ys = []
            for b in self.state.blocks_at_depth(int(self._depth.get())):
                xs += [b.x1, b.x2]
                ys += [b.y1, b.y2]
            pad = max(max(xs) - min(xs), max(ys) - min(ys), 1.0) * 0.12
            ax.set_xlim(min(xs) - pad, max(xs) + pad)
            ax.set_ylim(min(ys) - pad, max(ys) + pad)
        ax.set_aspect("equal")
        ax.grid(True, color="#e5e7eb", linewidth=0.5)
        ax.set_title("BUDA Floorplanner Prototype", fontsize=11)
        self._canvas.draw_idle()

    def _update_selection_label(self):
        name = self.state.selected
        if not name:
            self._sel_var.set("No block selected.")
            return
        try:
            b = self.state.block(name)
        except Exception:
            self._sel_var.set(name)
            return
        self._sel_var.set(
            f"{name}\n"
            f"({b.x1:.1f}, {b.y1:.1f}) - ({b.x2:.1f}, {b.y2:.1f})\n"
            f"{b.x2 - b.x1:.1f} x {b.y2 - b.y1:.1f}"
        )

    def _on_press(self, event):
        if event.inaxes != self._ax or event.button != 1:
            return
        tb = getattr(self._canvas, "toolbar", None)
        if tb and getattr(tb, "mode", ""):
            return
        for patch, name in self._patch_to_name.items():
            if patch.contains(event)[0]:
                b = self.state.block(name)
                self._select_name(name)
                self._drag = {
                    "name": name,
                    "dx": event.xdata - b.x1,
                    "dy": event.ydata - b.y1,
                }
                self._draw()
                return

    def _on_motion(self, event):
        if not self._drag or event.inaxes != self._ax:
            return
        name = self._drag["name"]
        raw_x = event.xdata - self._drag["dx"]
        raw_y = event.ydata - self._drag["dy"]
        fpc.move_block(self.state, name, raw_x, raw_y)
        self._draw()

    def _on_release(self, event):
        if self._drag:
            self._drag = None
            self._status.set("Block moved.")


def main():
    root = tk.Tk()
    app = BdbFloorplanner(root)
    if len(sys.argv) > 1:
        path = sys.argv[1]
        if os.path.exists(path):
            app.state = fpc.load_bdb(path)
            app._bdb_var.set(path)
            app._sync_canvas_vars()
            app._refresh_list()
            app._draw()
    root.mainloop()


if __name__ == "__main__":
    main()
