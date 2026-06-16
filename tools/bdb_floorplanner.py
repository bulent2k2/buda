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

# Add src/ to path so we can import buda_viz
_SRC = os.path.join(_HERE, "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import floorplanner_commands as fpc
import buda_viz


class BdbFloorplanner:
    def __init__(self, root):
        self.root = root
        self.root.title("BUDA Floorplanner Prototype")
        self.root.geometry("1360x820")

        # Bring window to front and set icon using centralized helpers
        buda_viz.set_icon(self.root, "buda_fp_icon.png")
        buda_viz.raise_window(self.root)

        self.state = fpc.new_state()
        self._patch_to_name: dict = {}
        self._handle_patches: list[tuple] = []   # (patch, name, corner_str)
        self._drag = None
        self._path: list[str] = []               # drill-down stack
        self._status = tk.StringVar(value="Open or create a BDB to begin.")

        self._bdb_var = tk.StringVar()
        self._die_w = tk.DoubleVar(value=2000.0)
        self._die_h = tk.DoubleVar(value=1200.0)
        self._grid = tk.DoubleVar(value=10.0)
        self._sel_var = tk.StringVar(value="")
        self._issue_var = tk.StringVar(value="")
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
        ttk.Button(setup, text="Apply", command=self._apply_canvas).grid(
            row=3, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        # Depth overlay control
        ov_f = ttk.Frame(setup)
        ov_f.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(4, 0))
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
        ttk.Button(filter_f, text="Add", command=self._add_block).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._align_mb = tk.Menubutton(filter_f, text="Align ▾", relief="raised")
        self._align_mb.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))
        align_menu = tk.Menu(self._align_mb, tearoff=False)
        self._align_mb["menu"] = align_menu
        align_menu.add_command(label="Top  ↑",    command=self._align_top)
        align_menu.add_command(label="Bottom ↓",  command=self._align_bottom)
        align_menu.add_command(label="Left  ←",   command=self._align_left)
        align_menu.add_command(label="Right →",   command=self._align_right)
        filter_f2 = ttk.Frame(blocks)
        filter_f2.pack(fill=tk.X, pady=(4, 0))
        ttk.Button(filter_f2, text="Optimize…",
                   command=self._open_optimize_dialog).pack(fill=tk.X)
        tv_frame = ttk.Frame(blocks)
        tv_frame.pack(fill=tk.BOTH, expand=True, pady=(4, 0))
        self._tree = ttk.Treeview(tv_frame, show="tree", selectmode="browse")
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
    def _spin(parent, label, var, row, from_=1, increment=10):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=1)
        ttk.Spinbox(parent, textvariable=var, from_=from_, to=1_000_000,
                    increment=increment, width=10).grid(
            row=row, column=1, sticky="ew", pady=1)
        parent.columnconfigure(1, weight=1)

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
        self.state.selected = None
        self._refresh_breadcrumbs()
        self._refresh_tree()
        self._draw()

    def _go_depth(self, depth: int):
        self._path = self._path[:depth]
        self.state.selected = None
        self._refresh_breadcrumbs()
        self._refresh_tree()
        self._draw()

    def _drill_into(self, name: str):
        self._path.append(name)
        self.state.selected = None
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
        self.state = fpc.load_bdb(path)
        self._path = []
        self._bdb_var.set(path)
        self._sync_canvas_vars()
        self._refresh_breadcrumbs()
        self._refresh_tree()
        self._draw()
        max_depth = max((n.count("/") for n in self.state.block_names), default=0)
        self._status.set(
            f"Loaded {len(self.state.block_names)} block(s), "
            f"{max_depth + 1} level(s).")

    def _new_bdb(self):
        path = filedialog.asksaveasfilename(defaultextension=".bdb",
                                            filetypes=[("BDB", "*.bdb"), ("All", "*")])
        if not path:
            return
        self.state = fpc.create_bdb(path, self._die_w.get(), self._die_h.get(), self._grid.get())
        self._path = []
        self._bdb_var.set(path)
        self._refresh_breadcrumbs()
        self._refresh_tree()
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
        self._path = []
        self._bdb_var.set(bdb_path)
        self._sync_canvas_vars()
        self._refresh_breadcrumbs()
        self._refresh_tree()
        self._draw()
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
        names = self._selected_tree_names()
        if len(names) < 2:
            self._status.set("Select at least two blocks to align.")
            return
        fn(self.state, names)
        self._draw()
        self._status.set(f"Aligned {len(names)} block(s) to {edge} edge.")

    def _align_bottom(self): self._do_align(fpc.align_bottom, "bottom")
    def _align_top(self):    self._do_align(fpc.align_top,    "top")
    def _align_left(self):   self._do_align(fpc.align_left,   "left")
    def _align_right(self):  self._do_align(fpc.align_right,  "right")

    def _open_optimize_dialog(self):
        if self.state is None:
            self._status.set("Open or create a BDB first.")
            return
        root_blocks = [n for n in self.state.block_names if "/" not in n]
        if not root_blocks:
            self._status.set("No root-level blocks to optimize.")
            return
        dlg = _OptimizeDialog(self.root, self.state, root_blocks, self._opt_settings)
        self.root.wait_window(dlg.top)
        if dlg.result is not None:
            self._draw()
            r = dlg.result
            self._status.set(
                f"Optimize: HPWL={r.hpwl:.1f}  overlap={r.overlap:.1f}  "
                f"({r.iterations} iter)"
            )

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
            self.state.selected = sel[0]
            self._draw()

    def _on_tree_dblclick(self, _event=None):
        sel = self._tree.selection()
        if sel:
            self._drill_into(sel[0])

    def _select_name(self, name: str):
        self.state.selected = name
        try:
            self._tree.selection_set(name)
            self._tree.see(name)
        except Exception:
            pass

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
            selected = block.name == self.state.selected
            patch = mpatches.Rectangle(
                (block.x1, block.y1), block.x2 - block.x1, block.y2 - block.y1,
                facecolor="#8ecae6" if selected else "#d9e8f5",
                edgecolor="#0f172a" if selected else "#475569",
                linewidth=2.0 if selected else 0.9,
                alpha=0.30 if extra_levels > 0 else 0.92, picker=True, zorder=2)
            ax.add_patch(patch)
            self._patch_to_name[patch] = name
            label = name.split("/")[-1]
            ax.text(block.x1 + 4, block.y1 + 4, label,
                    fontsize=7.5, color="#0f172a", va="bottom", clip_on=True, zorder=3)

            # Corner handles for selected block
            if selected:
                HS = max(vis_ref * 0.012, 1.0)
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
                    ax.text(cb.x1 + 2, cb.y1 + 2, child.split("/")[-1],
                            fontsize=7, color="#7c2d12", alpha=alpha,
                            clip_on=True, zorder=2.6)
                    if remaining > 1:
                        _draw_overlay(child, remaining - 1, alpha * 0.72)
            for name in visible:
                _draw_overlay(name, extra_levels, 0.85)

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

        ax.set_aspect("equal")
        ax.grid(True, color="#e5e7eb", linewidth=0.5)
        title = "BUDA Floorplanner"
        if self._path:
            title += " — " + " / ".join(n.split("/")[-1] for n in self._path)
        ax.set_title(title, fontsize=11)
        self._canvas.draw_idle()

    def _update_selection_label(self):
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
        if event.inaxes != self._ax or event.button != 1:
            return
        tb = getattr(self._canvas, "toolbar", None)
        if tb and getattr(tb, "mode", ""):
            return

        # 1. Check corner handles first
        for hp, name, corner in self._handle_patches:
            if hp.contains(event)[0]:
                self._drag = {"mode": "resize", "name": name, "corner": corner}
                return

        # 2. Check block body
        for patch, name in self._patch_to_name.items():
            if patch.contains(event)[0]:
                b = self.state.block(name)
                self._select_name(name)
                if event.dblclick:
                    self._draw()
                    self._drill_into(name)
                    return
                self._drag = {
                    "mode": "move",
                    "name": name,
                    "dx": event.xdata - b.x1,
                    "dy": event.ydata - b.y1,
                }
                self._draw()
                return

    def _on_motion(self, event):
        if not self._drag or event.inaxes != self._ax:
            return
        if event.xdata is None or event.ydata is None:
            return

        mode = self._drag.get("mode")
        name = self._drag["name"]

        if mode == "resize":
            corner = self._drag["corner"]
            b = self.state.block(name)
            x1, y1, x2, y2 = b.x1, b.y1, b.x2, b.y2
            if "l" in corner:
                x1 = event.xdata
            if "r" in corner:
                x2 = event.xdata
            if "t" in corner:
                y1 = event.ydata
            if "b" in corner:
                y2 = event.ydata
            fpc.resize_block(self.state, name, x1, y1, x2, y2)
            self._draw()

        elif mode == "move":
            raw_x = event.xdata - self._drag["dx"]
            raw_y = event.ydata - self._drag["dy"]
            fpc.move_block(self.state, name, raw_x, raw_y)
            self._draw()

    def _on_release(self, event):
        if not self._drag:
            return
        mode = self._drag.get("mode", "move")
        name = self._drag.get("name")
        self._drag = None
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
        ttk.Label(hdr_f, text="Block", width=20, anchor="w").pack(side=tk.LEFT)
        ttk.Label(hdr_f, text="Fixed",      width=7,  anchor="center").pack(side=tk.LEFT)
        ttk.Label(hdr_f, text="Reshapeable", width=12, anchor="center").pack(side=tk.LEFT)
        ttk.Label(hdr_f, text="Min W × H",  width=14, anchor="center").pack(side=tk.LEFT)

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

        for name in root_blocks:
            row = ttk.Frame(rows_f)
            row.pack(fill=tk.X, pady=1)

            ttk.Label(row, text=name, width=20, anchor="w").pack(side=tk.LEFT)

            fv = tk.BooleanVar(value=_s_fixed.get(name, False))
            self._fixed_vars[name] = fv
            ttk.Checkbutton(row, variable=fv).pack(side=tk.LEFT, padx=(10, 0))

            rv = tk.BooleanVar(value=_s_reshape.get(name, False))
            self._reshape_vars[name] = rv
            ttk.Checkbutton(row, variable=rv,
                            command=lambda n=name: self._on_reshape_toggle(n)).pack(
                side=tk.LEFT, padx=(20, 0))

            mw = tk.DoubleVar(value=_s_min_w.get(name, 0.0))
            mh = tk.DoubleVar(value=_s_min_h.get(name, 0.0))
            self._min_w_vars[name] = mw
            self._min_h_vars[name] = mh

            reshape_on = _s_reshape.get(name, False)
            sp_w = ttk.Spinbox(row, textvariable=mw, from_=0, to=99999,
                               increment=10, width=6,
                               state="normal" if reshape_on else "disabled")
            sp_w.pack(side=tk.LEFT, padx=(12, 0))
            self._min_w_spins[name] = sp_w

            ttk.Label(row, text="×").pack(side=tk.LEFT, padx=2)

            sp_h = ttk.Spinbox(row, textvariable=mh, from_=0, to=99999,
                               increment=10, width=6,
                               state="normal" if reshape_on else "disabled")
            sp_h.pack(side=tk.LEFT)
            self._min_h_spins[name] = sp_h

        # ── Buttons ───────────────────────────────────────────────────────────
        btn_f = ttk.Frame(self.top)
        btn_f.pack(fill=tk.X, padx=8, pady=8)
        ttk.Button(btn_f, text="Run",    command=self._run).pack(side=tk.RIGHT, padx=(4, 0))
        ttk.Button(btn_f, text="Cancel", command=self.top.destroy).pack(side=tk.RIGHT)

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

        self.result = fpc.optimize_placement(
            self.state, method=method,
            fixed=fixed, reshapeable=reshapeable,
            min_sizes=min_sizes or None,
            **kwargs,
        )
        self.top.destroy()


def main():
    root = tk.Tk()
    app = BdbFloorplanner(root)
    if len(sys.argv) > 1:
        path = sys.argv[1]
        if os.path.exists(path):
            app.state = fpc.load_bdb(path)
            app._path = []
            app._bdb_var.set(path)
            app._sync_canvas_vars()
            app._refresh_breadcrumbs()
            app._refresh_tree()
            app._draw()
    root.mainloop()


if __name__ == "__main__":
    main()
