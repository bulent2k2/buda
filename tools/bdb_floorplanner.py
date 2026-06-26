#!/usr/bin/env python3

# Copyright 2026 Ben Bulent Basaran
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
BUDA floorplanner prototype.

This is intentionally a thin Tk/Matplotlib frontend over the C++
FloorplannerEngine.  It is meant for quick manual placement experiments and
HBundle-flow script export, not as the final polished editor.
"""

from __future__ import annotations

import collections
import os
import sys
import tkinter as tk
import queue
import threading
from tkinter import filedialog, messagebox, simpledialog, ttk

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure
import matplotlib.patches as mpatches

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# Add src/ to path so we can import buda_viz
_SRC = os.path.join(_HERE, "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import floorplanner_commands as fpc
import buda_viz
from ui_state import ViewState


class BdbFloorplanner:
    def __init__(self, root):
        self.root = root
        self.root.title("BUDA Floorplanner Prototype")
        self.root.geometry("1360x820")

        # Bring window to front and set icon using centralized helpers
        buda_viz.set_icon(self.root, "buda_fp_icon.png")
        buda_viz.raise_window(self.root)

        self.state = fpc.new_state()
        self.ui_state = ViewState()
        self.ui_state.add_listener(self._draw)
        self._patch_to_name: dict = {}
        self._handle_patches: list[tuple] = []   # (patch, name, corner_str)
        self._drag = None
        self._rbpatch = None                     # live rubber-band zoom rectangle
        self._zoom_limits: tuple | None = None   # (xlim, ylim) set by manual zoom
        self._canvas_sel: set[str] = set()       # canvas multi-selection
        self._edge_mode: bool = False            # edge-selection mode
        self._edge_sel: list = []                # [(block, edge)], all l/r OR all t/b
        self._path: list[str] = []               # drill-down stack
        self._undo_stack: collections.deque = collections.deque(maxlen=50)
        self._redo_stack: list = []
        self._status = tk.StringVar(value="Open or create a BDB to begin.")

        self._bdb_var = tk.StringVar()
        self._die_w = tk.DoubleVar(value=2000.0)
        self._die_h = tk.DoubleVar(value=1200.0)
        self._grid = tk.DoubleVar(value=10.0)
        self._step = tk.DoubleVar(value=10.0)
        self._sel_var = tk.StringVar(value="")
        self._issue_var = tk.StringVar(value="")
        self._hpwl_var = tk.StringVar(value="")
        self._overlay_depth = tk.IntVar(value=0)   # extra depth levels to overlay

        self._opt_settings: dict = {
            "alg":         "sa",
            "iter":        20000,
            "w_wl":        1.0,
            "w_area":      0.1,
            "w_ovlp":      10.0,
            "fixed":       {},
            "reshapeable": {},
            "min_w":       {},
            "min_h":       {},
        }

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._build_ui()

    def _on_close(self):
        fpc.release_bdb_lock(self.state)
        self.root.destroy()

    def _apply_ro_state(self):
        """Reflect state.is_read_only in the window title and button states."""
        ro = getattr(self.state, "is_read_only", False)
        path = self.state.bdb_path or "BUDA Floorplanner"
        base = os.path.basename(path) if path else "BUDA Floorplanner"
        suffix = "  [read-only]" if ro else ""
        self.root.title(f"BUDA Floorplanner — {base}{suffix}")
        s = "disabled" if ro else "normal"
        self._write_btn.config(state=s)
        self._add_btn.config(state=s)
        self._align_mb.config(state=s)
        self._opt_btn.config(state=s)
        self._run_flow_btn.config(state=s)
        if ro:
            self._status.set(
                f"Opened read-only — another fp session holds the write lock on {base}.")

    def _build_ui(self):
        top = ttk.Frame(self.root, padding=(6, 4))
        top.pack(side=tk.TOP, fill=tk.X)
        ttk.Label(top, text="BDB:").grid(row=0, column=0, sticky="w")
        ttk.Entry(top, textvariable=self._bdb_var, width=64).grid(
            row=0, column=1, sticky="ew", padx=4)
        ttk.Button(top, text="Open", command=self._open_bdb).grid(row=0, column=2, padx=2)
        ttk.Button(top, text="New", command=self._new_bdb).grid(row=0, column=3, padx=2)
        ttk.Button(top, text="Import Verilog", command=self._import_verilog).grid(row=0, column=4, padx=2)
        self._write_btn = ttk.Button(top, text="Write", command=self._write_bdb)
        self._write_btn.grid(row=0, column=5, padx=2)
        ttk.Button(top, text="Export Flow", command=self._export_flow).grid(row=0, column=6, padx=2)
        self._run_flow_btn = ttk.Button(top, text="Run Flow", command=self._run_flow)
        self._run_flow_btn.grid(row=0, column=7, padx=2)
        top.columnconfigure(1, weight=1)

        # Breadcrumb bar
        self._crumb_frame = ttk.Frame(self.root, padding=(4, 2))
        self._crumb_frame.pack(side=tk.TOP, fill=tk.X)
        self._refresh_breadcrumbs()

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
        self._spin(setup, "Step", self._step, 3)
        ttk.Button(setup, text="Apply", command=self._apply_canvas).grid(
            row=4, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        # Depth overlay control
        ov_f = ttk.Frame(setup)
        ov_f.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        ttk.Label(ov_f, text="Overlay:").pack(side=tk.LEFT)
        ttk.Button(ov_f, text="−", width=2,
                   command=self._overlay_dec).pack(side=tk.LEFT, padx=2)
        self._overlay_lbl = ttk.Label(ov_f, text="0", width=2, anchor="center")
        self._overlay_lbl.pack(side=tk.LEFT)
        ttk.Button(ov_f, text="+", width=2,
                   command=self._overlay_inc).pack(side=tk.LEFT, padx=2)
        ttk.Label(ov_f, text="extra level(s)").pack(side=tk.LEFT, padx=(4, 0))

        blocks = ttk.LabelFrame(left, text="Blocks", padding=6)
        blocks.pack(fill=tk.BOTH, expand=True, pady=(0, 4))
        filter_f = ttk.Frame(blocks)
        filter_f.pack(fill=tk.X)
        self._add_btn = ttk.Button(filter_f, text="Add", command=self._add_block)
        self._add_btn.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._align_mb = tk.Menubutton(filter_f, text="Align ▾", relief="raised")
        self._align_mb.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))
        align_menu = tk.Menu(self._align_mb, tearoff=False)
        self._align_mb["menu"] = align_menu
        align_menu.add_command(label="Top  ↑",      command=self._align_top)
        align_menu.add_command(label="Bottom ↓",   command=self._align_bottom)
        align_menu.add_command(label="Left  ←",    command=self._align_left)
        align_menu.add_command(label="Right →",    command=self._align_right)
        align_menu.add_separator()
        align_menu.add_command(label="Distribute H", command=self._distribute_h)
        align_menu.add_command(label="Distribute V", command=self._distribute_v)
        align_menu.add_separator()
        align_menu.add_command(label="Center H  ↔", command=self._align_center_h)
        align_menu.add_command(label="Center V  ↕", command=self._align_center_v)
        align_menu.add_separator()
        align_menu.add_command(label="Rot 90°  ↻ CW",  command=self._rotate_cw)
        align_menu.add_command(label="Rot 90°  ↺ CCW", command=self._rotate_ccw)
        align_menu.add_separator()
        align_menu.add_command(label="Edges → Min",  command=lambda: self._do_align_edges("min"))
        align_menu.add_command(label="Edges → Max",  command=lambda: self._do_align_edges("max"))
        align_menu.add_command(label="Edges → Mean", command=lambda: self._do_align_edges("mean"))
        filter_f2 = ttk.Frame(blocks)
        filter_f2.pack(fill=tk.X, pady=(4, 0))
        self._opt_btn = ttk.Button(filter_f2, text="Optimize…",
                                   command=self._open_optimize_dialog)
        self._opt_btn.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._edge_mode_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(filter_f2, text="Edges", variable=self._edge_mode_var,
                        command=self._sync_edge_mode).pack(side=tk.LEFT, padx=(4, 0))
        tv_frame = ttk.Frame(blocks)
        tv_frame.pack(fill=tk.BOTH, expand=True, pady=(4, 0))
        self._tree = ttk.Treeview(tv_frame, show="tree", selectmode="extended")
        tv_sb = ttk.Scrollbar(tv_frame, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=tv_sb.set)
        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tv_sb.pack(side=tk.RIGHT, fill=tk.Y)
        self._tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self._tree.bind("<Double-Button-1>", self._on_tree_dblclick)

        props = ttk.LabelFrame(left, text="Selection", padding=6)
        props.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(props, textvariable=self._sel_var, anchor="w").pack(fill=tk.X)
        self._make_unique_btn = ttk.Button(props, text="Make Unique",
                                           command=self._on_make_unique)
        # Shown only when a replicated block is selected (packed in _update_selection_label)

        # View settings panel
        view_f = ttk.LabelFrame(left, text="View", padding=6)
        view_f.pack(fill=tk.X, pady=(0, 4))

        self._var_names = tk.BooleanVar(value=self.ui_state.block_names)
        self._var_names.trace_add("write", lambda *args: self._on_view_toggle('block_names', self._var_names))
        ttk.Checkbutton(view_f, text="Names", variable=self._var_names).pack(side=tk.LEFT, padx=2)

        checks = ttk.LabelFrame(left, text="Validation", padding=6)
        checks.pack(fill=tk.X)
        ttk.Button(checks, text="Validate", command=self._validate).pack(fill=tk.X)
        ttk.Label(checks, textvariable=self._issue_var, anchor="w", justify=tk.LEFT).pack(fill=tk.X, pady=(4, 0))
        ttk.Label(checks, textvariable=self._hpwl_var, anchor="w",
                  font=("TkDefaultFont", 9, "bold")).pack(fill=tk.X, pady=(2, 0))

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
        self._canvas.mpl_connect("key_press_event", self._on_key)

        # Arrow keys — modifier-aware routing via event.state.
        # Separate <Control-*> / <Meta-*> bindings don't work reliably on
        # macOS (Cmd+Arrow is delivered as plain <Arrow> with state bits set).
        # Read the state bitmask inside a single handler instead.
        #   no modifier        → nudge (move by Step)
        #   Ctrl or Cmd        → align
        #   Ctrl+Shift or Cmd+Shift → distribute
        self.root.bind("<Up>",    lambda e: self._on_arrow(e,  0,  1))
        self.root.bind("<Down>",  lambda e: self._on_arrow(e,  0, -1))
        self.root.bind("<Left>",  lambda e: self._on_arrow(e, -1,  0))
        self.root.bind("<Right>", lambda e: self._on_arrow(e,  1,  0))

        # Undo / Redo
        self.root.bind("<Control-z>", lambda e: self._undo())
        self.root.bind("<Control-Z>", lambda e: self._redo())
        self.root.bind("<Control-y>", lambda e: self._redo())
        self.root.bind("<Meta-z>",    lambda e: self._undo())
        self.root.bind("<Meta-Z>",    lambda e: self._redo())

        # Select all
        self.root.bind("<Control-a>", lambda e: self._select_all())
        self.root.bind("<Meta-a>",    lambda e: self._select_all())

        # f — toggle maximize (skip when a text widget has focus)
        self.root.bind("f", self._toggle_maximize)

        # h / H — reset zoom to full view
        self.root.bind("h", lambda e: self._home_view())
        self.root.bind("H", lambda e: self._home_view())

        # v — run validation
        self.root.bind("v", lambda e: self._validate_if_focused())

        # e — toggle edge-selection mode
        self.root.bind("e", lambda e: self._flip_edge_mode())

        # Esc — clear block selection
        self.root.bind("<Escape>", lambda e: self._clear_canvas_sel())

        ttk.Label(self.root, textvariable=self._status, relief=tk.SUNKEN,
                  anchor="w", padding=(6, 2)).pack(side=tk.BOTTOM, fill=tk.X)
        self._draw()

    def _on_view_toggle(self, prop: str, var: tk.BooleanVar):
        setattr(self.ui_state, prop, var.get())
        self.ui_state.notify()

    @staticmethod
    def _spin(parent, label, var, row, from_=1, increment=10):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=1)
        ttk.Spinbox(parent, textvariable=var, from_=from_, to=1_000_000,
                    increment=increment, width=10).grid(
            row=row, column=1, sticky="ew", pady=1)
        parent.columnconfigure(1, weight=1)

    def _toggle_maximize(self, _event=None):
        fw = self.root.focus_get()
        if isinstance(fw, (ttk.Spinbox, ttk.Entry, tk.Entry, tk.Spinbox, tk.Text)):
            return
        self.root.state("normal" if self.root.state() == "zoomed" else "zoomed")

    # ------------------------------------------------------------------
    # Overlay depth control
    # ------------------------------------------------------------------

    def _overlay_dec(self):
        v = max(0, self._overlay_depth.get() - 1)
        self._overlay_depth.set(v)
        self._overlay_lbl.configure(text=str(v))
        self._draw()

    def _overlay_inc(self):
        v = min(4, self._overlay_depth.get() + 1)
        self._overlay_depth.set(v)
        self._overlay_lbl.configure(text=str(v))
        self._draw()

    # ------------------------------------------------------------------
    # Breadcrumb helpers
    # ------------------------------------------------------------------

    def _refresh_breadcrumbs(self):
        for w in self._crumb_frame.winfo_children():
            w.destroy()
        ttk.Button(self._crumb_frame, text="[top]",
                   command=self._go_top).pack(side=tk.LEFT)
        for i, name in enumerate(self._path):
            ttk.Label(self._crumb_frame, text=" > ").pack(side=tk.LEFT)
            idx = i
            leaf = name.split("/")[-1]
            cell = fpc.get_block_cell(self.state, name)
            n = fpc.count_cell_instances(self.state, cell) if cell else 0
            label = leaf if n <= 1 else f"{leaf} (shared ×{n})"
            ttk.Button(self._crumb_frame, text=label,
                       command=lambda i=idx: self._go_depth(i + 1)).pack(side=tk.LEFT)

    def _go_top(self):
        self._path = []
        self._canvas_sel.clear()
        self._edge_sel = []
        self.state.selected = None
        self._zoom_limits = None
        self._refresh_breadcrumbs()
        self._refresh_tree()
        self._draw()

    def _go_depth(self, depth: int):
        self._path = self._path[:depth]
        self._canvas_sel.clear()
        self._edge_sel = []
        self.state.selected = None
        self._zoom_limits = None
        self._refresh_breadcrumbs()
        self._refresh_tree()
        self._draw()

    def _drill_into(self, name: str):
        self._path.append(name)
        self._canvas_sel.clear()
        self._edge_sel = []
        self.state.selected = None
        self._zoom_limits = None
        self._refresh_breadcrumbs()
        self._refresh_tree()
        self._draw()
        leaf = name.split("/")[-1]
        if not self._children_of(name):
            self._status.set(f"'{leaf}' has no sub-blocks — use Add to create them.")
        else:
            self._status.set(f"Viewing inside '{leaf}'.")

    def _children_of(self, name: str) -> list[str]:
        prefix = name + "/"
        depth = name.count("/") + 1
        return [n for n in self.state.block_names
                if n.startswith(prefix) and n.count("/") == depth]

    # ------------------------------------------------------------------
    # BDB commands
    # ------------------------------------------------------------------

    def _open_bdb(self):
        path = filedialog.askopenfilename(filetypes=[("BDB", "*.bdb"), ("All", "*")])
        if not path:
            return
        fpc.release_bdb_lock(self.state)
        self.state = fpc.load_bdb(path)
        self._path = []
        self._zoom_limits = None
        self._bdb_var.set(path)
        self._sync_canvas_vars()
        self._refresh_breadcrumbs()
        self._refresh_tree()
        self._draw()
        self._apply_ro_state()
        if not self.state.is_read_only:
            max_depth = max((n.count("/") for n in self.state.block_names), default=0)
            self._status.set(
                f"Loaded {len(self.state.block_names)} block(s), "
                f"{max_depth + 1} level(s).")

    def _new_bdb(self):
        path = filedialog.asksaveasfilename(defaultextension=".bdb",
                                            filetypes=[("BDB", "*.bdb"), ("All", "*")])
        if not path:
            return
        fpc.release_bdb_lock(self.state)
        self.state = fpc.create_bdb(path, self._die_w.get(), self._die_h.get(), self._grid.get())
        self._path = []
        self._zoom_limits = None
        self._bdb_var.set(path)
        self._refresh_breadcrumbs()
        self._refresh_tree()
        self._draw()
        self._apply_ro_state()
        if not self.state.is_read_only:
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
        try:
            new_state = fpc.import_verilog(
                v_path, bdb_path, self._die_w.get(), self._die_h.get(), self._grid.get())
        except PermissionError as exc:
            messagebox.showerror("Import Failed", str(exc))
            self._status.set(str(exc))
            return
        fpc.release_bdb_lock(self.state)   # Release only after successful import
        self.state = new_state
        self._path = []
        self._zoom_limits = None
        self._bdb_var.set(bdb_path)
        self._sync_canvas_vars()
        self._refresh_breadcrumbs()
        self._refresh_tree()
        self._draw()
        self._apply_ro_state()
        max_depth = max((n.count("/") for n in self.state.block_names), default=0)
        suffix = ""
        if self.state.unplaced_names:
            suffix = f" Seeded {len(self.state.unplaced_names)} placeholder block(s)."
        self._status.set(
            f"Imported Verilog from {os.path.basename(v_path)} — "
            f"{len(self.state.block_names)} block(s), "
            f"{max_depth + 1} level(s).{suffix}")

    def _apply_canvas(self):
        fpc.set_die(self.state, self._die_w.get(), self._die_h.get())
        fpc.set_grid(self.state, self._grid.get())
        self._draw()
        self._status.set("Canvas settings applied.")

    def _add_block(self):
        if self._path:
            # Drilled into a parent — add a child block using local coords.
            parent_name = self._path[-1]
            leaf_label  = parent_name.split("/")[-1]
            raw = simpledialog.askstring(
                "Add Sub-block", f"Name (inside '{leaf_label}'):", parent=self.root)
            if not raw:
                return
            full_name = parent_name + "/" + raw
            try:
                pb = self.state.engine.get_block(parent_name)
            except Exception:
                self._status.set(f"Cannot locate parent block '{parent_name}'.")
                return
            x = simpledialog.askfloat("Add Sub-block", "X (absolute):",
                                      initialvalue=pb.x1 + 20.0, parent=self.root)
            y = simpledialog.askfloat("Add Sub-block", "Y (absolute):",
                                      initialvalue=pb.y1 + 20.0, parent=self.root)
            w = simpledialog.askfloat("Add Sub-block", "Width:",
                                      initialvalue=100.0, parent=self.root)
            h = simpledialog.askfloat("Add Sub-block", "Height:",
                                      initialvalue=80.0, parent=self.root)
            if None in (x, y, w, h):
                return
            fpc.add_child_block(self.state, full_name,
                                x - pb.x1, y - pb.y1, w, h)
            self._refresh_tree()
            self._select_name(full_name)
            self._draw()
        else:
            name = simpledialog.askstring("Add Block", "Instance path:", parent=self.root)
            if not name:
                return
            x = simpledialog.askfloat("Add Block", "X origin:",
                                      initialvalue=100.0, parent=self.root)
            y = simpledialog.askfloat("Add Block", "Y origin:",
                                      initialvalue=100.0, parent=self.root)
            w = simpledialog.askfloat("Add Block", "Width:",
                                      initialvalue=200.0, parent=self.root)
            h = simpledialog.askfloat("Add Block", "Height:",
                                      initialvalue=160.0, parent=self.root)
            if None in (x, y, w, h):
                return
            fpc.add_block(self.state, name, x, y, w, h)
            self._refresh_tree()
            self._select_name(name)
            self._draw()

    def _do_align(self, fn, edge: str):
        names = list(self._canvas_sel | set(self._selected_tree_names()))
        if len(names) < 2:
            self._status.set("Select at least two blocks to align.")
            return
        self._push_undo(self._snapshot())
        fn(self.state, names)
        self._draw()
        self._status.set(f"Aligned {len(names)} block(s) to {edge} edge.")

    def _align_bottom(self): self._do_align(fpc.align_bottom, "bottom")
    def _align_top(self):    self._do_align(fpc.align_top,    "top")
    def _align_left(self):   self._do_align(fpc.align_left,   "left")
    def _align_right(self):  self._do_align(fpc.align_right,  "right")

    def _do_distribute(self, fn, direction: str):
        names = list(self._canvas_sel | set(self._selected_tree_names()))
        if len(names) < 3:
            self._status.set("Select at least three blocks to distribute.")
            return
        self._push_undo(self._snapshot())
        fn(self.state, names)
        self._draw()
        self._status.set(f"Distributed {len(names)} block(s) {direction}.")

    def _distribute_h(self): self._do_distribute(fpc.distribute_h, "horizontally")
    def _distribute_v(self): self._do_distribute(fpc.distribute_v, "vertically")

    def _align_center_h(self): self._do_align(fpc.align_center_h, "horizontal center")
    def _align_center_v(self): self._do_align(fpc.align_center_v, "vertical center")

    def _do_rotate(self, fn, label: str):
        names = list(self._canvas_sel | set(self._selected_tree_names()))
        if not names or self.state is None:
            self._status.set("Select at least one block to rotate.")
            return
        self._push_undo(self._snapshot())
        fn(self.state, names)
        self._draw()
        self._status.set(f"Rotated {len(names)} block(s) {label}.")

    def _rotate_cw(self):  self._do_rotate(fpc.rotate_blocks_cw,  "90° CW")
    def _rotate_ccw(self): self._do_rotate(fpc.rotate_blocks_ccw, "90° CCW")

    def _open_optimize_dialog(self):
        if self.state is None:
            self._status.set("Open or create a BDB first.")
            return
        root_blocks = [n for n in self.state.block_names if "/" not in n]
        if not root_blocks:
            self._status.set("No root-level blocks to optimize.")
            return
        snap = self._snapshot()
        dlg = _OptimizeDialog(self.root, self.state, root_blocks, self._opt_settings)
        self.root.wait_window(dlg.top)
        if dlg.result is not None:
            self._push_undo(snap)
            self._draw()
            r = dlg.result
            self._status.set(
                f"Optimize: HPWL={r.hpwl:.1f}  overlap={r.overlap:.1f}  "
                f"({r.iterations} iter)"
            )

    def _validate_if_focused(self):
        """Run validate only when no text widget has keyboard focus (avoids eating 'v' in entry fields)."""
        fw = self.root.focus_get()
        if isinstance(fw, (ttk.Spinbox, ttk.Entry, tk.Entry, tk.Spinbox, tk.Text)):
            return
        self._validate()

    def _validate(self):
        issues = fpc.validate(self.state)
        step = self._step.get()
        gaps = fpc.find_gap_violations(self.state, self._visible_names(), step)

        lines = []
        # Overlap / out-of-die issues (up to 5)
        for issue in issues[:5]:
            if issue.kind == "OVERLAP":
                lines.append(f"OVERLAP  {issue.block_a} / {issue.block_b}")
            elif issue.kind == "OUTSIDE_DIE":
                lines.append(f"OUTSIDE  {issue.block_a}")
            else:
                lines.append(f"{issue.kind}  {issue.message}")
        if len(issues) > 5:
            lines.append(f"  … {len(issues) - 5} more overlap/outside issue(s)")

        # Gap violations — one line per block, largest gap first
        for name, gap, arrow, neighbor in gaps[:8]:
            leaf = name.split("/")[-1]
            nb_leaf = neighbor.split("/")[-1]
            lines.append(f"GAP  {leaf}: {gap:.0f} {arrow} {nb_leaf}")
        if len(gaps) > 8:
            lines.append(f"  … {len(gaps) - 8} more gap violation(s)")

        total = len(issues) + len(gaps)
        if not lines:
            self._issue_var.set("No issues.")
            self._status.set("Floorplan validation passed.")
        else:
            self._issue_var.set("\n".join(lines))
            self._status.set(
                f"Validation: {len(issues)} overlap/outside, {len(gaps)} gap violation(s)."
            )

    def _write_bdb(self):
        if not self.state.bdb_path:
            path = self._bdb_var.get().strip()
            if not path:
                self._new_bdb()
                return
            self.state.bdb_path = path
        try:
            fpc.write_bdb(self.state)
            self._status.set(f"Placements written to {self.state.bdb_path}.")
        except Exception as exc:
            messagebox.showerror("Write Failed",
                                 f"Could not write BDB:\n{exc}")
            self._status.set(f"Write failed: {exc}")

    def _export_flow(self):
        path = filedialog.asksaveasfilename(defaultextension=".buda",
                                            filetypes=[("BUDA script", "*.buda"), ("All", "*")])
        if not path:
            return
        depth = max(1, len(self._path) if self._path else 1)
        fpc.export_hbundle_script(self.state, path, depth=depth)
        self._status.set(f"Exported HBundle flow script to {path}.")

    def _run_flow(self):
        if not self.state.bdb_path:
            self._status.set("Create, open, or import a BDB before running flow.")
            return
        self._status.set("Running HBundle flow...")
        self.root.update_idletasks()
        depth = max(1, len(self._path) if self._path else 1)
        result = fpc.run_hbundle_flow(self.state, depth=depth)
        tail = (result.stdout or result.stderr).strip().splitlines()[-1:] or [""]
        if result.returncode == 0:
            self._status.set(f"HBundle flow completed. {tail[0]}")
        else:
            self._status.set(f"HBundle flow failed ({result.returncode}). {tail[0]}")

    # ------------------------------------------------------------------
    # List / selection helpers
    # ------------------------------------------------------------------

    def _sync_canvas_vars(self):
        if self.state.engine.die_w() > 0:
            self._die_w.set(self.state.engine.die_w())
        if self.state.engine.die_h() > 0:
            self._die_h.set(self.state.engine.die_h())
        self._grid.set(self.state.engine.grid())

    def _refresh_tree(self):
        self._tree.delete(*self._tree.get_children())
        roots = fpc.build_hierarchy_tree(self.state)

        def _insert(node: fpc.BlockNode, parent_iid: str = ""):
            iid = self._tree.insert(
                parent_iid, "end", iid=node.name,
                text=node.label, open=(node.depth < 2))
            for child in node.children:
                _insert(child, iid)

        for root in roots:
            _insert(root)

        if self.state.selected:
            try:
                self._tree.selection_set(self.state.selected)
                self._tree.see(self.state.selected)
            except Exception:
                pass

    def _visible_names(self) -> list[str]:
        if not self._path:
            return self.state.names_at_depth(0)
        prefix = self._path[-1] + "/"
        depth = len(self._path)
        return [n for n in self.state.block_names
                if n.startswith(prefix) and n.count("/") == depth]

    def _selected_tree_names(self) -> list[str]:
        return list(self._tree.selection())

    def _on_tree_select(self, _event=None):
        sel = self._tree.selection()
        if sel:
            self._canvas_sel = set(sel)
            self.state.selected = sel[0]
            self._draw()

    def _on_tree_dblclick(self, _event=None):
        sel = self._tree.selection()
        if sel:
            self._drill_into(sel[0])

    def _select_name(self, name: str):
        self._canvas_sel = {name}
        self.state.selected = name
        try:
            self._tree.selection_set(name)
            self._tree.see(name)
        except Exception:
            pass

    def _select_all(self):
        """Select every visible block (current drill-down level)."""
        if self.state is None:
            return
        visible = self._visible_names()
        if not visible:
            return
        self._canvas_sel = set(visible)
        self.state.selected = visible[0]
        try:
            self._tree.selection_set(visible)
        except Exception:
            pass
        self._draw()

    # ------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------

    def _draw(self):
        ax = self._ax
        ax.clear()
        self._patch_to_name.clear()
        self._handle_patches.clear()

        dw, dh = self.state.engine.die_w(), self.state.engine.die_h()

        # Auto-zoom: if drilling down, fit to parent block; else fit to die
        zoom_set = False
        vis_ref = max(dw, dh) if dw > 0 else 200.0   # handle-size reference
        if self._path:
            try:
                pb = self.state.block(self._path[-1])
                pw, ph = pb.x2 - pb.x1, pb.y2 - pb.y1
                vis_ref = max(pw, ph)
                margin = max(pw, ph) * 0.05
                ax.set_xlim(pb.x1 - margin, pb.x2 + margin)
                ax.set_ylim(pb.y1 - margin, pb.y2 + margin)
                zoom_set = True
                # Draw parent as faint background
                ax.add_patch(mpatches.Rectangle(
                    (pb.x1, pb.y1), pw, ph,
                    facecolor="#e5e7eb", edgecolor="#9ca3af",
                    linewidth=1.0, linestyle="--", zorder=1))
            except Exception:
                pass

        if dw > 0 and dh > 0:
            ax.add_patch(mpatches.Rectangle(
                (0, 0), dw, dh, facecolor="#f8fafc", edgecolor="#6b7280",
                linewidth=1.2, zorder=0))
            if not zoom_set:
                margin = max(dw, dh) * 0.04
                ax.set_xlim(-margin, dw + margin)
                ax.set_ylim(-margin, dh + margin)

        visible = self._visible_names()
        extra_levels = self._overlay_depth.get()
        
        for name in visible:
            try:
                block = self.state.block(name)
            except Exception:
                continue
            selected  = block.name == self.state.selected
            in_sel    = block.name in self._canvas_sel and not selected
            fc = "#8ecae6" if selected else ("#bae6fd" if in_sel else "#d9e8f5")
            ec = "#0f172a" if selected else ("#1e40af" if in_sel else "#475569")
            lw = 2.0      if selected else (1.5       if in_sel else 0.9)
            patch = mpatches.Rectangle(
                (block.x1, block.y1), block.x2 - block.x1, block.y2 - block.y1,
                facecolor=fc, edgecolor=ec, linewidth=lw,
                alpha=0.30 if extra_levels > 0 else 0.92, picker=True, zorder=2)
            ax.add_patch(patch)
            self._patch_to_name[patch] = name
            
            if self.ui_state.block_names:
                label = name.split("/")[-1]
                ax.text(block.x1 + 4, block.y1 + 4, label,
                        fontsize=7.5, color="#0f172a", va="bottom", clip_on=True, zorder=3)
            
            # Corner + mid-edge handles for selected block
            if selected:
                HS = max(vis_ref * 0.005, 1.0)
                mx = (block.x1 + block.x2) / 2
                my = (block.y1 + block.y2) / 2
                # Corner handles (square, white) — drag moves two edges at once
                for corner, (cx, cy) in [
                        ("tl", (block.x1, block.y1)),
                        ("tr", (block.x2, block.y1)),
                        ("bl", (block.x1, block.y2)),
                        ("br", (block.x2, block.y2))]:
                    hp = mpatches.Rectangle(
                        (cx - HS, cy - HS), 2 * HS, 2 * HS,
                        facecolor="white", edgecolor="#0f172a",
                        linewidth=1.0, zorder=5, picker=True)
                    ax.add_patch(hp)
                    self._handle_patches.append((hp, name, corner))
                # Mid-edge handles (diamond, cyan) — drag moves one edge only
                for edge, (cx, cy) in [
                        ("l",  (block.x1, my)),
                        ("r",  (block.x2, my)),
                        ("t",  (mx, block.y1)),
                        ("b",  (mx, block.y2))]:
                    diamond = mpatches.RegularPolygon(
                        (cx, cy), numVertices=4, radius=HS * 1.3,
                        orientation=0,
                        facecolor="#22d3ee", edgecolor="#0e7490",
                        linewidth=1.0, zorder=5, picker=True)
                    ax.add_patch(diamond)
                    self._handle_patches.append((diamond, name, edge))

            # Edge mode: mid-edge handles on EVERY block; selected edges amber.
            if self._edge_mode:
                HS = max(vis_ref * 0.005, 1.0)
                mx = (block.x1 + block.x2) / 2
                my = (block.y1 + block.y2) / 2
                for edge, (cx, cy) in [
                        ("l",  (block.x1, my)),
                        ("r",  (block.x2, my)),
                        ("t",  (mx, block.y1)),
                        ("b",  (mx, block.y2))]:
                    is_sel = (name, edge) in self._edge_sel
                    if is_sel:
                        # Bold amber line over the edge itself.
                        if edge == "l":   xs, ys = [block.x1, block.x1], [block.y1, block.y2]
                        elif edge == "r": xs, ys = [block.x2, block.x2], [block.y1, block.y2]
                        elif edge == "t": xs, ys = [block.x1, block.x2], [block.y1, block.y1]
                        else:             xs, ys = [block.x1, block.x2], [block.y2, block.y2]
                        ax.plot(xs, ys, color="#f59e0b", linewidth=3.0, zorder=4)
                    diamond = mpatches.RegularPolygon(
                        (cx, cy), numVertices=4, radius=HS * 1.3,
                        orientation=0,
                        facecolor="#fbbf24" if is_sel else "#22d3ee",
                        edgecolor="#b45309" if is_sel else "#0e7490",
                        linewidth=1.0, zorder=5, picker=True)
                    ax.add_patch(diamond)
                    self._handle_patches.append((diamond, name, edge))

        # Depth overlay: draw child blocks above parents so they are visible
        if extra_levels > 0:
            def _draw_overlay(parent_name: str, remaining: int, alpha: float):
                for child in self._children_of(parent_name):
                    try:
                        cb = self.state.block(child)
                    except Exception:
                        continue
                    ax.add_patch(mpatches.Rectangle(
                        (cb.x1, cb.y1), cb.x2 - cb.x1, cb.y2 - cb.y1,
                        facecolor="#fed7aa", edgecolor="#ea580c",
                        linewidth=1.8, alpha=alpha, picker=False, zorder=2.5))
                    if self.ui_state.block_names:
                        ax.text(cb.x1 + 2, cb.y1 + 2, child.split("/")[-1],
                                fontsize=7, color="#7c2d12", alpha=alpha,
                                clip_on=True, zorder=2.6)
                    if remaining > 1:
                        _draw_overlay(child, remaining - 1, alpha * 0.72)
            for name in visible:
                _draw_overlay(name, extra_levels, 0.85)

        self._draw_flylines(ax)
        self._update_selection_label()
        if not zoom_set:
            if dw > 0 and dh > 0:
                pass  # already set above
            elif visible:
                xs, ys = [], []
                for name in visible:
                    try:
                        b = self.state.block(name)
                        xs += [b.x1, b.x2]
                        ys += [b.y1, b.y2]
                    except Exception:
                        pass
                if xs:
                    pad = max(max(xs) - min(xs), max(ys) - min(ys), 1.0) * 0.12
                    ax.set_xlim(min(xs) - pad, max(xs) + pad)
                    ax.set_ylim(min(ys) - pad, max(ys) + pad)

        # Restore manual zoom (set via bbox-zoom or scroll) unless we just
        # navigated (drill-in/out/home clears _zoom_limits first).
        if self._zoom_limits is not None:
            ax.set_xlim(*self._zoom_limits[0])
            ax.set_ylim(*self._zoom_limits[1])

        ax.set_aspect("equal")
        ax.grid(True, color="#e5e7eb", linewidth=0.5)
        title = "BUDA Floorplanner"
        if self._path:
            title += " — " + " / ".join(n.split("/")[-1] for n in self._path)
        ax.set_title(title, fontsize=11)
        self._canvas.draw_idle()

        # Live HPWL
        if self.state is not None and self.state.bdb is not None:
            hpwl = fpc.compute_hpwl(self.state)
            self._hpwl_var.set(f"HPWL: {hpwl:.1f}")
        else:
            self._hpwl_var.set("")

    # State-bitmask constants for modifier detection in <Key> events.
    # 0x0001 = Shift
    # 0x0004 = Control (all platforms)
    # 0x0008 = Mod1   — Cmd on macOS (most Tk 8.6 builds), Alt on some Linux
    # 0x0010 = Mod2   — Cmd on some older macOS Tk builds; Num Lock on Linux
    # Only include Mod1/Mod2 on macOS — on Linux Mod2 is Num Lock, not Cmd.
    _MOD_CTRL_CMD = 0x4 | (0x8 | 0x10 if sys.platform == "darwin" else 0)

    def _home_view(self) -> None:
        """Reset the canvas to full auto-fit view."""
        fw = self.root.focus_get()
        if isinstance(fw, (ttk.Spinbox, ttk.Entry, tk.Entry, tk.Spinbox, tk.Text)):
            return
        self._zoom_limits = None
        self._draw()
        self._status.set("Home: full view.")

    def _clear_canvas_sel(self) -> None:
        """Deselect all blocks (or, in edge mode with a selection, clear edges)."""
        if self._edge_mode and self._edge_sel:
            self._edge_sel = []
            self._draw()
            self._status.set("Edge selection cleared.")
            return
        self._canvas_sel.clear()
        if self.state is not None:
            self.state.selected = None
        try:
            self._tree.selection_set([])
        except Exception:
            pass
        self._draw()

    # ------------------------------------------------------------------
    # Edge-selection mode
    # ------------------------------------------------------------------

    @staticmethod
    def _edge_orient(edge: str) -> str:
        return "V" if edge in ("l", "r") else "H"

    def _flip_edge_mode(self) -> None:
        """Key handler for 'e' — flips the checkbutton var (single source of truth)."""
        fw = self.root.focus_get()
        if isinstance(fw, (ttk.Spinbox, ttk.Entry, tk.Entry, tk.Spinbox, tk.Text)):
            return
        self._edge_mode_var.set(not self._edge_mode_var.get())
        self._sync_edge_mode()

    def _sync_edge_mode(self) -> None:
        """Checkbutton command + called by _flip_edge_mode. Var drives _edge_mode."""
        self._edge_mode = self._edge_mode_var.get()
        if self._edge_mode:
            # Edge and block selections are mutually exclusive.
            self._canvas_sel.clear()
            if self.state is not None:
                self.state.selected = None
            self._status.set("Edge mode ON — click block edges to select (all V or all H).")
        else:
            self._edge_sel = []
            self._status.set("Edge mode OFF.")
        self._draw()

    def _toggle_edge_in_sel(self, name: str, edge: str) -> None:
        """Add/remove (name, edge) from the edge selection, enforcing all-V or all-H."""
        if self._edge_sel and self._edge_orient(edge) != self._edge_orient(self._edge_sel[0][1]):
            self._edge_sel = [(name, edge)]
            self._status.set(
                f"Orientation changed — edge selection restarted "
                f"({self._edge_orient(edge)}).")
        elif (name, edge) in self._edge_sel:
            self._edge_sel.remove((name, edge))
        else:
            self._edge_sel.append((name, edge))
        n = len(self._edge_sel)
        if n:
            orient = self._edge_orient(self._edge_sel[0][1])
            self._status.set(f"{n} edge(s) selected ({orient}).")
        self._draw()

    def _edge_hit_test(self, event):
        """Return (name, edge) for the clicked edge: a mid-edge diamond under the
        cursor, else the nearest edge of the block under the cursor; else None."""
        # 1. Diamond handle directly hit.
        for hp, name, edge in self._handle_patches:
            if edge in ("l", "r", "t", "b") and hp.contains(event)[0]:
                return (name, edge)
        # 2. Nearest edge of the block under the cursor.
        for patch, name in self._patch_to_name.items():
            if patch.contains(event)[0]:
                try:
                    b = self.state.block(name)
                except Exception:
                    return None
                dists = {"l": abs(event.xdata - b.x1), "r": abs(event.xdata - b.x2),
                         "t": abs(event.ydata - b.y1), "b": abs(event.ydata - b.y2)}
                return (name, min(dists, key=dists.get))
        return None

    def _sync_edges_to_instances(self) -> int:
        """Propagate each edited block's new size to its shared-cell siblings,
        mirroring the single-handle resize sync on release.  Returns the total
        synced instance count (0 if no shared cells)."""
        total = 0
        for name in sorted({n for n, _ in self._edge_sel}):
            try:
                b = self.state.block(name)
            except Exception:
                continue
            _cell, n = fpc.sync_cell_to_instances(
                self.state, name, b.x1, b.y1, b.x2, b.y2)
            if n > 1:
                total += n
        if total:
            self._refresh_tree()
        return total

    def _move_edges_by(self, delta: float) -> None:
        """Shift the selected edges by delta (arrow-key path)."""
        if self.state is None or not self._edge_sel:
            return
        self._push_undo(self._snapshot())
        fpc.move_edges(self.state, self._edge_sel, delta)
        synced = self._sync_edges_to_instances()
        self._draw()
        msg = f"Moved {len(self._edge_sel)} edge(s) by {delta:+.0f}."
        if synced:
            msg += f"  Synced {synced} shared instance(s)."
        self._status.set(msg)

    def _do_align_edges(self, mode: str) -> None:
        if not self._edge_mode or not self._edge_sel:
            self._status.set("Select edges first (press e, then click block edges).")
            return
        self._push_undo(self._snapshot())
        fpc.align_edges(self.state, self._edge_sel, mode)
        synced = self._sync_edges_to_instances()
        self._draw()
        msg = f"Aligned {len(self._edge_sel)} edge(s) → {mode}."
        if synced:
            msg += f"  Synced {synced} shared instance(s)."
        self._status.set(msg)

    def _restore_silent(self, snap: dict) -> None:
        """Restore block bboxes from a snapshot without redrawing."""
        if not snap:
            return
        for name, (x1, y1, x2, y2) in snap.items():
            try:
                self.state.engine.resize_block_raw(name, x1, y1, x2, y2)
            except Exception:
                pass

    def _pan_view(self, dx: int, dy: int) -> None:
        """Pan the canvas by 15 % of the current view span per keypress."""
        ax = self._ax
        xlim = ax.get_xlim()
        ylim = ax.get_ylim()
        xpan = (xlim[1] - xlim[0]) * 0.15 * dx
        ypan = (ylim[1] - ylim[0]) * 0.15 * dy
        new_xlim = (xlim[0] + xpan, xlim[1] + xpan)
        new_ylim = (ylim[0] + ypan, ylim[1] + ypan)
        ax.set_xlim(*new_xlim)
        ax.set_ylim(*new_ylim)
        self._zoom_limits = (new_xlim, new_ylim)
        self._fig.canvas.draw_idle()

    def _on_arrow(self, event, dx: int, dy: int) -> None:
        """Route arrow key to move / align / distribute / pan based on modifier state."""
        fw = self.root.focus_get()
        if isinstance(fw, (ttk.Spinbox, ttk.Entry, tk.Entry, tk.Spinbox, tk.Text)):
            return
        state = getattr(event, 'state', 0)
        shift        = bool(state & 0x1)
        ctrl_or_cmd  = bool(state & self._MOD_CTRL_CMD)
        if self._edge_mode and self._edge_sel:
            # Edge mode: V edges move with ←/→, H edges with ↑/↓.
            axis = self._edge_orient(self._edge_sel[0][1])
            step = self._step.get()
            if   axis == "V" and dx != 0: self._move_edges_by(dx * step)
            elif axis == "H" and dy != 0: self._move_edges_by(dy * step)
            return
        if ctrl_or_cmd and shift:
            if dx != 0:
                self._distribute_h()
            else:
                self._distribute_v()
        elif ctrl_or_cmd:
            if   dx < 0: self._align_left()
            elif dx > 0: self._align_right()
            elif dy > 0: self._align_top()
            else:        self._align_bottom()
        elif self._canvas_sel:
            self._arrow_move(dx * self._step.get(), dy * self._step.get())
        else:
            self._pan_view(dx, dy)

    def _arrow_move(self, dx: float, dy: float) -> None:
        """Nudge all canvas-selected blocks; ignored when a text widget has focus."""
        fw = self.root.focus_get()
        if isinstance(fw, (ttk.Spinbox, ttk.Entry, tk.Entry, tk.Spinbox, tk.Text)):
            return
        if self.state is None or not self._canvas_sel:
            return
        self._push_undo(self._snapshot())
        for name in self._canvas_sel:
            try:
                b = self.state.block(name)
                fpc.move_block(self.state, name, b.x1 + dx, b.y1 + dy)
            except Exception:
                pass
        self._draw()
        if self.state.selected:
            try:
                b = self.state.block(self.state.selected)
                label = self.state.selected.split("/")[-1]
                self._status.set(f"{label}: ({b.x1:.0f}, {b.y1:.0f})")
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Undo / Redo
    # ------------------------------------------------------------------

    def _snapshot(self) -> dict:
        """Capture current block positions from the live engine."""
        snap = {}
        if self.state is None:
            return snap
        for name in self.state.block_names:
            try:
                b = self.state.engine.get_block(name)
                snap[name] = (b.x1, b.y1, b.x2, b.y2)
            except Exception:
                pass
        return snap

    def _push_undo(self, snap: dict) -> None:
        self._undo_stack.append(snap)
        self._redo_stack.clear()

    def _restore(self, snap: dict) -> None:
        for name, (x1, y1, x2, y2) in snap.items():
            try:
                self.state.engine.resize_block_raw(name, x1, y1, x2, y2)
            except Exception:
                pass
        self._draw()

    def _undo(self) -> None:
        if not self._undo_stack:
            self._status.set("Nothing to undo.")
            return
        self._redo_stack.append(self._snapshot())
        self._restore(self._undo_stack.pop())
        self._status.set(f"Undo — {len(self._undo_stack)} step(s) remaining.")

    def _redo(self) -> None:
        if not self._redo_stack:
            self._status.set("Nothing to redo.")
            return
        self._undo_stack.append(self._snapshot())
        self._restore(self._redo_stack.pop())
        self._status.set(f"Redo — {len(self._redo_stack)} step(s) remaining.")

    def _draw_flylines(self, ax) -> None:
        """Draw dashed lines from selected block to every connected block, with net counts.

        Connectivity (which net_ids link which component ids) comes from the BDB,
        which stores the pin→net→component graph and is never mutated by the optimizer.
        Block *positions* come from state.engine, which is always up-to-date (the
        optimizer calls engine.resize_block_raw after each run).
        """
        if self.state is None or self.state.selected is None:
            return
        if self.state.bdb is None:
            return
        sel_name = self.state.selected

        # BDB gives us the id↔name mapping and the pin connectivity graph.
        comp_by_id = {c.id: c for c in self.state.bdb.all_components()}
        sel_comp = next((c for c in comp_by_id.values() if c.name == sel_name), None)
        if sel_comp is None:
            return

        # Positions come from the live engine (updated by optimizer / drag).
        def _center(name: str):
            try:
                b = self.state.engine.get_block(name)
                return (b.x1 + b.x2) / 2, (b.y1 + b.y2) / 2
            except Exception:
                return None

        sel_center = _center(sel_name)
        if sel_center is None:
            return
        sel_cx, sel_cy = sel_center

        # Collect net_ids that include the selected block
        sel_nets: set[int] = set()
        for p in self.state.bdb.all_pins():
            if p.comp_id == sel_comp.id:
                sel_nets.add(p.net_id)
        if not sel_nets:
            return

        # Count distinct net_ids per other component
        other_nets: dict[int, set[int]] = {}  # comp_id → set of net_ids
        for p in self.state.bdb.all_pins():
            if p.net_id in sel_nets and p.comp_id != sel_comp.id:
                other_nets.setdefault(p.comp_id, set()).add(p.net_id)

        for cid, nets in other_nets.items():
            other = comp_by_id.get(cid)
            if other is None:
                continue
            other_center = _center(other.name)
            if other_center is None:
                continue
            ocx, ocy = other_center
            count = len(nets)
            ax.plot([sel_cx, ocx], [sel_cy, ocy],
                    color="#f97316", alpha=0.55, linewidth=0.9,
                    linestyle="--", zorder=2)
            mx, my = (sel_cx + ocx) / 2, (sel_cy + ocy) / 2
            ax.annotate(str(count), (mx, my), fontsize=6.5,
                        color="#c2410c", ha="center", va="center",
                        bbox=dict(boxstyle="round,pad=0.15",
                                  fc="white", alpha=0.80, ec="none"),
                        zorder=4)

    def _update_selection_label(self):
        if self._edge_mode:
            n = len(self._edge_sel)
            orient = self._edge_orient(self._edge_sel[0][1]) if n else "-"
            self._sel_var.set(f"Edge mode: {n} edge(s) selected ({orient}).")
            self._make_unique_btn.pack_forget()
            return
        if len(self._canvas_sel) > 1:
            self._sel_var.set(f"{len(self._canvas_sel)} blocks selected.")
            self._make_unique_btn.pack_forget()
            return
        name = self.state.selected
        if not name:
            self._sel_var.set("No block selected.")
            self._make_unique_btn.pack_forget()
            return
        try:
            b = self.state.block(name)
        except Exception:
            self._sel_var.set(name)
            self._make_unique_btn.pack_forget()
            return
        n_children = len(self._children_of(name))
        child_hint = f"  [{n_children} children]" if n_children else ""
        cell = fpc.get_block_cell(self.state, name)
        n_inst = fpc.count_cell_instances(self.state, cell) if cell else 0
        if n_inst > 1:
            cell_hint = f"\n⚠ Shared: {cell} (×{n_inst})"
            self._make_unique_btn.pack(fill=tk.X, pady=(4, 0))
        else:
            cell_hint = f"\nCell: {cell}" if cell else ""
            self._make_unique_btn.pack_forget()
        self._sel_var.set(
            f"{name}{child_hint}{cell_hint}\n"
            f"({b.x1:.1f}, {b.y1:.1f}) - ({b.x2:.1f}, {b.y2:.1f})\n"
            f"{b.x2 - b.x1:.1f} x {b.y2 - b.y1:.1f}"
        )

    def _on_make_unique(self):
        name = self.state.selected
        if not name:
            return
        new_cell = fpc.make_block_unique(self.state, name)
        if new_cell:
            self._status.set(
                f"{name.split('/')[-1]} is now unique — cell: {new_cell}.")
        else:
            self._status.set(f"{name.split('/')[-1]} is already unique.")
        self._draw()

    # ------------------------------------------------------------------
    # Mouse events
    # ------------------------------------------------------------------

    def _on_press(self, event):
        if event.inaxes != self._ax:
            return
        tb = getattr(self._canvas, "toolbar", None)
        if tb and getattr(tb, "mode", ""):
            return

        # Right-click drag → rubber-band zoom box
        if event.button == 3:
            if event.xdata is not None and event.ydata is not None:
                self._drag = {"mode": "bbox_zoom",
                              "x0": event.xdata, "y0": event.ydata}
            return

        if event.button != 1:
            return

        # Edge mode: clicks select/move edges, not blocks.
        if self._edge_mode:
            hit = self._edge_hit_test(event)
            if hit is None:
                if self._edge_sel:
                    self._edge_sel = []
                    self._draw()
                return
            name, edge = hit
            if (name, edge) in self._edge_sel:
                # Begin a group drag (or, on zero motion, toggle this edge off).
                self._drag = {"mode": "edge_move",
                              "axis": "x" if edge in ("l", "r") else "y",
                              "x0": event.xdata, "y0": event.ydata,
                              "name": name, "edge": edge,
                              "snap": self._snapshot(), "moved": False}
            else:
                self._toggle_edge_in_sel(name, edge)
            return

        # Detect Shift or Ctrl/Cmd via the underlying tkinter event's state bitmask.
        # Either modifier turns a click into a toggle (add/remove from selection).
        gui_ev = getattr(event, "guiEvent", None)
        ev_state = getattr(gui_ev, "state", 0) if gui_ev else 0
        shift_held = bool(ev_state & 0x1) or bool(ev_state & self._MOD_CTRL_CMD)

        # 1. Check corner handles first
        for hp, name, corner in self._handle_patches:
            if hp.contains(event)[0]:
                self._drag = {"mode": "resize", "name": name, "corner": corner,
                              "snap": self._snapshot()}
                return

        # 2. Check block body
        for patch, name in self._patch_to_name.items():
            if patch.contains(event)[0]:
                b = self.state.block(name)
                if event.dblclick:
                    self._select_name(name)
                    self._draw()
                    self._drill_into(name)
                    return
                if shift_held:
                    # Toggle this block in the canvas multi-selection
                    if name in self._canvas_sel:
                        self._canvas_sel.discard(name)
                        self.state.selected = next(iter(self._canvas_sel), None)
                    else:
                        self._canvas_sel.add(name)
                        self.state.selected = name
                    try:
                        self._tree.selection_set(list(self._canvas_sel))
                    except Exception:
                        pass
                    self._draw()
                    return
                # Plain click: single-select and start drag
                self._select_name(name)
                self._drag = {
                    "mode": "move",
                    "name": name,
                    "dx": event.xdata - b.x1,
                    "dy": event.ydata - b.y1,
                    "snap": self._snapshot(),
                }
                self._draw()
                return

        # Click on empty canvas: clear canvas selection (unless shift held)
        if not shift_held:
            self._canvas_sel.clear()
            self.state.selected = None
            self._draw()

    # ------------------------------------------------------------------
    # Snapping helpers
    # ------------------------------------------------------------------

    def _snap(self, val: float, candidates: list, grid: float,
              view_span: float) -> float:
        """Snap val to the nearest candidate within 4 % of view_span, else to grid."""
        threshold = view_span * 0.04
        best, best_d = None, threshold
        for c in candidates:
            d = abs(val - c)
            if d < best_d:
                best, best_d = c, d
        return best if best is not None else round(val / grid) * grid if grid > 0 else val

    @staticmethod
    def _excl_set(exclude):
        """Normalize an exclude arg (None / str / iterable of names) to a set."""
        if exclude is None:
            return set()
        if isinstance(exclude, str):
            return {exclude}
        return set(exclude)

    def _hanan_xs(self, exclude=None) -> list:
        """All block x1/x2 coordinates (+ die boundaries) for X-axis snapping.
        `exclude` may be a single block name or an iterable of names to omit."""
        excl = self._excl_set(exclude)
        xs = [0.0, self.state.engine.die_w()]
        for n in self.state.block_names:
            if n in excl:
                continue
            try:
                b = self.state.block(n)
                xs.extend([b.x1, b.x2])
            except Exception:
                pass
        return xs

    def _hanan_ys(self, exclude=None) -> list:
        """All block y1/y2 coordinates (+ die boundaries) for Y-axis snapping.
        `exclude` may be a single block name or an iterable of names to omit."""
        excl = self._excl_set(exclude)
        ys = [0.0, self.state.engine.die_h()]
        for n in self.state.block_names:
            if n in excl:
                continue
            try:
                b = self.state.block(n)
                ys.extend([b.y1, b.y2])
            except Exception:
                pass
        return ys

    # ------------------------------------------------------------------

    def _on_motion(self, event):
        if not self._drag or event.inaxes != self._ax:
            return
        if event.xdata is None or event.ydata is None:
            return

        mode = self._drag.get("mode")

        if mode == "bbox_zoom":
            x0, y0 = self._drag["x0"], self._drag["y0"]
            x1, y1 = event.xdata, event.ydata
            # Remove previous rubber-band patch without a full redraw
            if self._rbpatch is not None:
                self._rbpatch.remove()
                self._rbpatch = None
            zoom_in = x1 > x0
            color = "#0ea5e9" if zoom_in else "#f97316"  # blue=in, orange=out
            self._rbpatch = mpatches.Rectangle(
                (min(x0, x1), min(y0, y1)),
                abs(x1 - x0), abs(y1 - y0),
                linewidth=1.5, edgecolor=color, facecolor=color,
                alpha=0.15, linestyle="--", zorder=10)
            self._ax.add_patch(self._rbpatch)
            self._fig.canvas.draw_idle()
            return

        name = self._drag["name"]

        if mode == "resize":
            corner = self._drag["corner"]
            b = self.state.block(name)
            x1, y1, x2, y2 = b.x1, b.y1, b.x2, b.y2
            grid = self.state.engine.grid()
            xlim = self._ax.get_xlim()
            ylim = self._ax.get_ylim()
            xspan = xlim[1] - xlim[0]
            yspan = ylim[1] - ylim[0]
            hxs = self._hanan_xs(exclude=name)
            hys = self._hanan_ys(exclude=name)
            if "l" in corner:
                x1 = self._snap(event.xdata, hxs, grid, xspan)
            if "r" in corner:
                x2 = self._snap(event.xdata, hxs, grid, xspan)
            if "t" in corner:
                y1 = self._snap(event.ydata, hys, grid, yspan)
            if "b" in corner:
                y2 = self._snap(event.ydata, hys, grid, yspan)
            fpc.resize_block(self.state, name, x1, y1, x2, y2)
            self._draw()

        elif mode == "move":
            raw_x = event.xdata - self._drag["dx"]
            raw_y = event.ydata - self._drag["dy"]
            fpc.move_block(self.state, name, raw_x, raw_y)
            self._draw()

        elif mode == "edge_move":
            grid = self.state.engine.grid()
            # Exclude the dragged blocks so their own (moving) edges aren't snap
            # targets — otherwise edges stick to themselves then jump.
            excl = {n for n, _ in self._edge_sel}
            if self._drag["axis"] == "x":
                xlim = self._ax.get_xlim()
                target = self._snap(event.xdata, self._hanan_xs(exclude=excl),
                                    grid, xlim[1] - xlim[0])
                delta = target - self._drag["x0"]
            else:
                ylim = self._ax.get_ylim()
                target = self._snap(event.ydata, self._hanan_ys(exclude=excl),
                                    grid, ylim[1] - ylim[0])
                delta = target - self._drag["y0"]
            if abs(delta) > 0:
                self._drag["moved"] = True
            # Restore the pre-drag state, then apply the absolute delta (no drift).
            self._restore_silent(self._drag["snap"])
            fpc.move_edges(self.state, self._edge_sel, delta)
            self._draw()

    def _on_release(self, event):
        if not self._drag:
            return
        mode = self._drag.get("mode", "move")

        if mode == "bbox_zoom":
            if self._rbpatch is not None:
                self._rbpatch.remove()
                self._rbpatch = None
            x0, y0 = self._drag["x0"], self._drag["y0"]
            self._drag = None
            if event.inaxes == self._ax and event.xdata is not None:
                self._apply_bbox_zoom(x0, y0, event.xdata, event.ydata)
            else:
                self._fig.canvas.draw_idle()
            return

        if mode == "edge_move":
            moved = self._drag.get("moved", False)
            snap = self._drag.get("snap")
            ename, eedge = self._drag.get("name"), self._drag.get("edge")
            self._drag = None
            if moved:
                if snap is not None:
                    self._push_undo(snap)
                synced = self._sync_edges_to_instances()
                self._draw()
                msg = f"Moved {len(self._edge_sel)} edge(s)."
                if synced:
                    msg += f"  Synced {synced} shared instance(s)."
                self._status.set(msg)
            else:
                # Zero-motion click on a selected edge → toggle it off.
                if (ename, eedge) in self._edge_sel:
                    self._edge_sel.remove((ename, eedge))
                self._draw()
                self._status.set(f"{len(self._edge_sel)} edge(s) selected.")
            return

        name = self._drag.get("name")
        snap = self._drag.get("snap")
        self._drag = None
        if snap is not None:
            self._push_undo(snap)
        if mode == "resize" and name:
            b = self.state.block(name)
            cell, n = fpc.sync_cell_to_instances(
                self.state, name, b.x1, b.y1, b.x2, b.y2)
            if n > 1:
                self._status.set(
                    f"Resized {name.split('/')[-1]}; "
                    f"synced [{cell}] to {n} instances.")
            else:
                self._status.set("Block resized.")
            self._refresh_tree()
            self._draw()
        elif mode == "move" and name:
            b = self.state.block(name)
            parent_cell, n = fpc.sync_move_to_instances(
                self.state, name, b.x1, b.y1)
            if n > 1:
                self._status.set(
                    f"Moved {name.split('/')[-1]}; "
                    f"synced [{parent_cell}] → {n} parent instances.")
            else:
                self._status.set("Block moved.")
            self._refresh_tree()
            self._draw()

    def _ax_pixel_aspect(self) -> float:
        """Return axes-window pixel aspect ratio (width / height)."""
        bbox = self._ax.get_window_extent()
        return bbox.width / bbox.height if bbox.height > 0 else 1.0

    def _apply_bbox_zoom(self, x0: float, y0: float, x1: float, y1: float):
        """Apply a rubber-band zoom.

        LR drag (x1 > x0): zoom IN — set the view to the drawn box.
        RL drag (x1 < x0): zoom OUT — expand the view so that the current
            viewport would appear at the size of the drawn box.
        A degenerate box (skinny in either dimension) is ignored.
        """
        ax = self._ax
        xlim = ax.get_xlim()
        ylim = ax.get_ylim()
        box_w = abs(x1 - x0)
        box_h = abs(y1 - y0)
        view_w = xlim[1] - xlim[0]
        view_h = ylim[1] - ylim[0]
        # Ignore clicks that barely moved or are skinny in either dimension
        if box_w < view_w * 0.01 or box_h < view_h * 0.01:
            self._fig.canvas.draw_idle()
            return

        # Expand the box to match the axes pixel aspect so the result fills the
        # full window without set_aspect("equal") adding whitespace margins.
        ax_aspect = self._ax_pixel_aspect()   # px_w / px_h
        cx = (x0 + x1) / 2
        cy = (y0 + y1) / 2
        if box_w / box_h < ax_aspect:
            # Box is taller than the window — widen it
            box_w = box_h * ax_aspect
        else:
            # Box is wider than the window — heighten it
            box_h = box_w / ax_aspect

        if x1 > x0:
            # Zoom IN: fit the (aspect-corrected) box to the viewport
            new_xlim = (cx - box_w / 2, cx + box_w / 2)
            new_ylim = (cy - box_h / 2, cy + box_h / 2)
            ax.set_xlim(*new_xlim)
            ax.set_ylim(*new_ylim)
            self._zoom_limits = (new_xlim, new_ylim)
            self._status.set("Zoomed in. Right-drag RL to zoom out; z/Z or scroll to step.")
        else:
            # Zoom OUT: current view expands so it fits in the drawn box.
            fx = view_w / box_w if box_w > 0 else 1.0
            fy = view_h / box_h if box_h > 0 else 1.0
            f  = max(fx, fy)
            vcx = (xlim[0] + xlim[1]) / 2
            vcy = (ylim[0] + ylim[1]) / 2
            new_xlim = (vcx - view_w * f / 2, vcx + view_w * f / 2)
            new_ylim = (vcy - view_h * f / 2, vcy + view_h * f / 2)
            ax.set_xlim(*new_xlim)
            ax.set_ylim(*new_ylim)
            self._zoom_limits = (new_xlim, new_ylim)
            self._status.set("Zoomed out. Right-drag LR to zoom in.")
        self._fig.canvas.draw_idle()

    def _zoom(self, event, zoom_in: bool):
        """Zoom the canvas in or out, centred on the cursor (or view centre)."""
        ax = self._ax
        scale = 0.8 if zoom_in else 1.25
        x, y = event.xdata, event.ydata
        if event.inaxes != ax or x is None or y is None:
            xlim = ax.get_xlim()
            ylim = ax.get_ylim()
            x = (xlim[0] + xlim[1]) / 2
            y = (ylim[0] + ylim[1]) / 2
        xlim = ax.get_xlim()
        ylim = ax.get_ylim()
        new_xlim = (x - (x - xlim[0]) * scale, x + (xlim[1] - x) * scale)
        new_ylim = (y - (y - ylim[0]) * scale, y + (ylim[1] - y) * scale)
        ax.set_xlim(*new_xlim)
        ax.set_ylim(*new_ylim)
        self._zoom_limits = (new_xlim, new_ylim)
        self._fig.canvas.draw_idle()

    def _on_key(self, event):
        """Handle matplotlib canvas key events (zoom only — other keys via Tk)."""
        if event.key == "z": self._zoom(event, zoom_in=True)
        if event.key == "Z": self._zoom(event, zoom_in=False)


class _OptimizeDialog:
    """Modal dialog for configuring and launching the placement optimizer."""

    def __init__(self, parent, state, root_blocks: list[str], settings: dict):
        self.state    = state
        self.result   = None
        self._settings = settings

        self.top = tk.Toplevel(parent)
        self.top.title("Optimize Placement")
        self.top.resizable(False, False)
        self.top.grab_set()  # modal

        pad = dict(padx=6, pady=3)

        # ── Algorithm ────────────────────────────────────────────────────────
        alg_f = ttk.LabelFrame(self.top, text="Algorithm", padding=6)
        alg_f.pack(fill=tk.X, padx=8, pady=(8, 0))
        self._alg = tk.StringVar(value=settings.get("alg", "sa"))
        ttk.Radiobutton(alg_f, text="SA  (Simulated Annealing)",
                        variable=self._alg, value="sa",
                        command=self._on_alg_change).pack(anchor="w")
        ttk.Radiobutton(alg_f, text="GA  (Genetic Algorithm)",
                        variable=self._alg, value="ga",
                        command=self._on_alg_change).pack(anchor="w")

        iter_f = ttk.Frame(alg_f)
        iter_f.pack(fill=tk.X, pady=(4, 0))
        alg_val = settings.get("alg", "sa")
        self._iter_lbl = ttk.Label(iter_f,
                                   text="Iterations:" if alg_val == "sa" else "Generations:")
        self._iter_lbl.pack(side=tk.LEFT)
        self._iter_var = tk.IntVar(value=settings.get("iter", 20000))
        ttk.Spinbox(iter_f, textvariable=self._iter_var,
                    from_=100, to=500000, increment=1000, width=9).pack(side=tk.LEFT, padx=(4, 0))

        # ── Weights ───────────────────────────────────────────────────────────
        wt_f = ttk.LabelFrame(self.top, text="Weights", padding=6)
        wt_f.pack(fill=tk.X, padx=8, pady=(6, 0))
        self._w_wl   = tk.DoubleVar(value=settings.get("w_wl",   1.0))
        self._w_area = tk.DoubleVar(value=settings.get("w_area", 0.1))
        self._w_ovlp = tk.DoubleVar(value=settings.get("w_ovlp", 10.0))
        for col, (lbl, var) in enumerate([
                ("Wire-length", self._w_wl),
                ("Area", self._w_area),
                ("Overlap", self._w_ovlp)]):
            ttk.Label(wt_f, text=lbl + ":").grid(row=0, column=col * 2, sticky="w", **pad)
            ttk.Spinbox(wt_f, textvariable=var,
                        from_=0.0, to=100.0, increment=0.1,
                        format="%.2f", width=6).grid(row=0, column=col * 2 + 1, **pad)

        # ── Block constraints ─────────────────────────────────────────────────
        bc_f = ttk.LabelFrame(self.top, text="Block Constraints", padding=6)
        bc_f.pack(fill=tk.BOTH, expand=True, padx=8, pady=(6, 0))

        hdr_f = ttk.Frame(bc_f)
        hdr_f.pack(fill=tk.X)
        hdr_f.columnconfigure(0, minsize=160)
        hdr_f.columnconfigure(1, minsize=60)
        hdr_f.columnconfigure(2, minsize=90)
        ttk.Label(hdr_f, text="Block",       anchor="w"     ).grid(row=0, column=0, sticky="w", padx=2)
        ttk.Label(hdr_f, text="Fixed",       anchor="center").grid(row=0, column=1)
        ttk.Label(hdr_f, text="Reshapeable", anchor="center").grid(row=0, column=2)
        ttk.Label(hdr_f, text="Min Width",   anchor="center").grid(row=0, column=3, padx=(4, 0))
        ttk.Label(hdr_f, text="Min Height",  anchor="center").grid(row=0, column=5)

        scroll_canvas = tk.Canvas(bc_f, height=min(len(root_blocks) * 28 + 4, 200),
                                  bd=0, highlightthickness=0)
        vsb = ttk.Scrollbar(bc_f, orient="vertical", command=scroll_canvas.yview)
        scroll_canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        scroll_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        rows_f = ttk.Frame(scroll_canvas)
        scroll_canvas.create_window((0, 0), window=rows_f, anchor="nw")
        rows_f.bind("<Configure>",
                    lambda e: scroll_canvas.configure(
                        scrollregion=scroll_canvas.bbox("all")))
        rows_f.columnconfigure(0, minsize=160)
        rows_f.columnconfigure(1, minsize=60)
        rows_f.columnconfigure(2, minsize=90)

        self._fixed_vars:    dict[str, tk.BooleanVar] = {}
        self._reshape_vars:  dict[str, tk.BooleanVar] = {}
        self._min_w_vars:    dict[str, tk.DoubleVar]  = {}
        self._min_h_vars:    dict[str, tk.DoubleVar]  = {}
        self._min_w_spins:   dict[str, ttk.Spinbox]  = {}
        self._min_h_spins:   dict[str, ttk.Spinbox]  = {}

        _s_fixed = settings.get("fixed",       {})
        _s_reshape = settings.get("reshapeable", {})
        _s_min_w   = settings.get("min_w",        {})
        _s_min_h   = settings.get("min_h",        {})

        for i, name in enumerate(root_blocks):
            fv = tk.BooleanVar(value=_s_fixed.get(name, False))
            self._fixed_vars[name] = fv
            rv = tk.BooleanVar(value=_s_reshape.get(name, False))
            self._reshape_vars[name] = rv
            mw = tk.DoubleVar(value=_s_min_w.get(name, 0.0))
            mh = tk.DoubleVar(value=_s_min_h.get(name, 0.0))
            self._min_w_vars[name] = mw
            self._min_h_vars[name] = mh

            reshape_on = _s_reshape.get(name, False)
            ttk.Label(rows_f, text=name, anchor="w").grid(
                row=i, column=0, sticky="w", padx=2, pady=1)
            ttk.Checkbutton(rows_f, variable=fv).grid(
                row=i, column=1, pady=1)
            ttk.Checkbutton(rows_f, variable=rv,
                            command=lambda n=name: self._on_reshape_toggle(n)).grid(
                row=i, column=2, pady=1)
            sp_w = ttk.Spinbox(rows_f, textvariable=mw, from_=0, to=99999,
                               increment=10, width=6,
                               state="normal" if reshape_on else "disabled")
            sp_w.grid(row=i, column=3, padx=(4, 0), pady=1)
            self._min_w_spins[name] = sp_w
            ttk.Label(rows_f, text="×").grid(row=i, column=4, padx=2, pady=1)
            sp_h = ttk.Spinbox(rows_f, textvariable=mh, from_=0, to=99999,
                               increment=10, width=6,
                               state="normal" if reshape_on else "disabled")
            sp_h.grid(row=i, column=5, pady=1)
            self._min_h_spins[name] = sp_h

        # ── Buttons + inline progress bar ─────────────────────────────────────
        btn_f = ttk.Frame(self.top)
        self._btn_f = btn_f
        btn_f.pack(fill=tk.X, padx=8, pady=8)
        self._run_btn = ttk.Button(btn_f, text="Run", command=self._run)
        self._run_btn.pack(side=tk.RIGHT, padx=(4, 0))
        ttk.Button(btn_f, text="Cancel", command=self.top.destroy).pack(side=tk.RIGHT)
        # Progress widgets — packed into btn_f (left side) only while running
        self._progress_var = tk.IntVar(value=0)
        self._prog_bar = ttk.Progressbar(btn_f, variable=self._progress_var,
                                          maximum=100, length=160)
        self._prog_lbl = ttk.Label(btn_f, text="", width=10)

    def _on_alg_change(self):
        self._iter_lbl.config(
            text="Iterations:" if self._alg.get() == "sa" else "Generations:")
        self._iter_var.set(20000 if self._alg.get() == "sa" else 200)

    def _on_reshape_toggle(self, name: str):
        enabled = self._reshape_vars[name].get()
        state = "normal" if enabled else "disabled"
        self._min_w_spins[name].config(state=state)
        self._min_h_spins[name].config(state=state)

    def _run(self):
        fixed      = [n for n, v in self._fixed_vars.items()   if v.get()]
        reshapeable = [n for n, v in self._reshape_vars.items() if v.get()]
        min_sizes  = {
            n: (self._min_w_vars[n].get(), self._min_h_vars[n].get())
            for n in reshapeable
            if self._min_w_vars[n].get() > 0 or self._min_h_vars[n].get() > 0
        }
        method = self._alg.get()
        kwargs: dict = dict(seed=42)
        if method == "sa":
            kwargs["max_iter"] = self._iter_var.get()
        else:
            kwargs["generations"] = self._iter_var.get()
        kwargs["w_wl"]   = self._w_wl.get()
        kwargs["w_area"] = self._w_area.get()
        kwargs["w_ovlp"] = self._w_ovlp.get()

        # Persist settings so the next dialog open pre-fills them.
        self._settings.update({
            "alg":         method,
            "iter":        self._iter_var.get(),
            "w_wl":        self._w_wl.get(),
            "w_area":      self._w_area.get(),
            "w_ovlp":      self._w_ovlp.get(),
            "fixed":       {n: v.get() for n, v in self._fixed_vars.items()},
            "reshapeable": {n: v.get() for n, v in self._reshape_vars.items()},
            "min_w":       {n: v.get() for n, v in self._min_w_vars.items()},
            "min_h":       {n: v.get() for n, v in self._min_h_vars.items()},
        })

        # Disable Run button and show progress bar
        self._run_btn.config(state="disabled")
        self._prog_bar.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._prog_lbl.pack(side=tk.LEFT, padx=(6, 0))
        self._progress_var.set(0)
        self._prog_lbl.config(text="0%")

        q: queue.SimpleQueue = queue.SimpleQueue()
        total = kwargs.get("max_iter") or kwargs.get("generations") or 100

        def progress_cb(cur: int, tot: int) -> None:
            pct = int(100 * cur / tot) if tot else 0
            q.put(("progress", pct, cur, tot))

        kwargs["progress_fn"] = progress_cb

        state   = self.state
        m_sizes = min_sizes or None

        def worker() -> None:
            try:
                result = fpc.optimize_placement(
                    state, method=method,
                    fixed=fixed, reshapeable=reshapeable,
                    min_sizes=m_sizes, **kwargs)
                q.put(("done", result))
            except Exception as exc:
                q.put(("error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()
        self._poll(q)

    def _poll(self, q: queue.SimpleQueue) -> None:
        try:
            while True:
                msg = q.get_nowait()
                kind = msg[0]
                if kind == "progress":
                    _, pct, cur, tot = msg
                    self._progress_var.set(pct)
                    self._prog_lbl.config(text=f"{pct}%")
                elif kind == "done":
                    self.result = msg[1]
                    self.top.destroy()
                    return
                elif kind == "error":
                    messagebox.showerror("Optimizer Error", msg[1], parent=self.top)
                    self._run_btn.config(state="normal")
                    self._prog_bar.pack_forget()
                    self._prog_lbl.pack_forget()
                    return
        except queue.Empty:
            pass
        self.top.after(100, lambda: self._poll(q))


def main():
    root = tk.Tk()
    app = BdbFloorplanner(root)
    if len(sys.argv) > 1:
        path = sys.argv[1]
        if not os.path.exists(path):
            messagebox.showerror("File Not Found",
                                 f"Cannot open BDB — file not found:\n{path}")
            root.destroy()
            return
        app.state = fpc.load_bdb(path)
        app._path = []
        app._zoom_limits = None
        app._bdb_var.set(path)
        app._sync_canvas_vars()
        app._refresh_breadcrumbs()
        app._refresh_tree()
        app._draw()
        app._apply_ro_state()
    buda_viz.raise_window(root)
    root.mainloop()


if __name__ == "__main__":
    main()
