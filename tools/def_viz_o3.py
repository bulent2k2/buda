#!/usr/bin/env python3
"""
DEF Visualizer — V3: Canvas-first groups.

Groups are created directly from canvas/instance selections.
A compact groups list (name + color swatch + checkbox) sits at the bottom of
the left column.  Clicking a group box on the canvas selects it and shows its
instances in the Instances panel.  Sub-groups: select a group in the list, then
'Add sub-group' nests it under the selected group.

Usage: python3 def_viz_v3.py [file.def [file.lef]]
"""

import os, sys, re, tkinter as tk
from tkinter import ttk, filedialog, simpledialog

import matplotlib
matplotlib.use('TkAgg')
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure
import matplotlib.patches as mpatches

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
from def_viz_shared import (DefVizData,
                             draw_die, draw_bg_instances,
                             draw_selected_instances, draw_group_boxes, fit_view)
from group_tree import GroupTree, lighten_color


class DefVizV3:
    def __init__(self, root):
        self.root  = root
        root.title('DEF Viz — V3: Canvas-first Groups')
        root.geometry('1480x860')

        self.data          = DefVizData()
        self.selected_nets = set()
        self._pending_ipc_msg = None
        self._net_items    = []
        self._inst_items   = []
        self._patch_inst   = {}
        self._patch_group  = {}
        self._hover_ann    = None
        self._updating     = False
        self._depth_var    = tk.IntVar(value=0)
        self._hl_active    = False

        # {gid: BooleanVar} — per-group visibility
        self._grp_vis:  dict[str, tk.BooleanVar] = {}
        self._sel_gid:  str = None   # currently selected group
        self._canvas_sel_insts: set = set()  # insts rubber-banded on canvas

        # Group utilities
        self._utils_win     = None
        self._rect_selector = None
        self._rect_active   = False

        self._build_ui()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = self.root

        # ── Top bar: file paths ───────────────────────────────────────────────
        top = ttk.Frame(root, padding=(6,4)); top.pack(side=tk.TOP, fill=tk.X)
        ttk.Label(top, text='DEF:').grid(row=0, column=0, sticky='w', padx=(0,4))
        self._def_var = tk.StringVar()
        ttk.Entry(top, textvariable=self._def_var, width=60).grid(row=0, column=1, sticky='ew', padx=2)
        ttk.Button(top, text='Browse…', command=self._browse_def).grid(row=0, column=2, padx=4)
        ttk.Label(top, text='LEF:').grid(row=1, column=0, sticky='w', padx=(0,4))
        self._lef_var = tk.StringVar()
        ttk.Entry(top, textvariable=self._lef_var, width=60).grid(row=1, column=1, sticky='ew', padx=2)
        ttk.Button(top, text='Browse…', command=self._browse_lef).grid(row=1, column=2, padx=4)
        ttk.Button(top, text='Load', command=self._load, width=10).grid(
            row=0, column=3, rowspan=2, padx=10, sticky='ns')
        top.columnconfigure(1, weight=1)

        # ── Status bar ────────────────────────────────────────────────────────
        self._status = tk.StringVar(value='Load a DEF/LEF to begin.')
        ttk.Label(root, textvariable=self._status, relief=tk.SUNKEN,
                  anchor='w', padding=(6,2)).pack(side=tk.BOTTOM, fill=tk.X)

        # ── Main area ─────────────────────────────────────────────────────────
        main = ttk.Frame(root); main.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=4, pady=2)

        # ── Column 1: Nets (top) + Instances (bottom), vertically resizable ───
        col1 = ttk.Frame(main, width=215); col1.pack(side=tk.LEFT, fill=tk.Y, padx=(0,3))
        col1.pack_propagate(False)

        vpane = ttk.PanedWindow(col1, orient=tk.VERTICAL)
        vpane.pack(fill=tk.BOTH, expand=True)

        # Nets panel
        np = ttk.LabelFrame(vpane, text='Nets', padding=4)
        vpane.add(np, weight=1)
        nf = ttk.Frame(np); nf.pack(fill=tk.X, pady=(0,2))
        ttk.Label(nf, text='Filter:').pack(side=tk.LEFT)
        self._net_filter = tk.StringVar()
        self._net_filter.trace_add('write', lambda *_: self._refresh_net_list())
        ttk.Entry(nf, textvariable=self._net_filter).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4,0))
        nlb_f = ttk.Frame(np); nlb_f.pack(fill=tk.BOTH, expand=True)
        self._net_lb = tk.Listbox(nlb_f, selectmode=tk.EXTENDED, width=24,
                                   exportselection=False, font=('Courier', 9))
        nsb = ttk.Scrollbar(nlb_f, orient=tk.VERTICAL, command=self._net_lb.yview)
        self._net_lb.configure(yscrollcommand=nsb.set)
        self._net_lb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True); nsb.pack(side=tk.LEFT, fill=tk.Y)
        self._net_lb.bind('<<ListboxSelect>>', self._on_net_select)
        ttk.Button(np, text='Clear selection', command=self._clear_selection).pack(fill=tk.X, pady=(4,0))
        depth_f = ttk.Frame(np); depth_f.pack(fill=tk.X, pady=(4,0))
        ttk.Label(depth_f, text='Depth:').pack(side=tk.LEFT)
        self._depth_sb = ttk.Spinbox(depth_f, textvariable=self._depth_var,
                                      from_=0, to=10, width=4,
                                      command=self._draw_canvas)
        self._depth_sb.pack(side=tk.LEFT, padx=4)
        self._depth_sb.bind('<Return>', lambda _: self._draw_canvas())
        self._depth_sb.bind('<FocusOut>', lambda _: self._draw_canvas())
        ttk.Button(np, text='Highlight Nets', command=self._highlight_nets).pack(fill=tk.X, pady=(2,0))

        # Instances panel
        ip = ttk.LabelFrame(vpane, text='Instances', padding=4)
        vpane.add(ip, weight=1)
        iff = ttk.Frame(ip); iff.pack(fill=tk.X, pady=(0,2))
        ttk.Label(iff, text='Filter:').pack(side=tk.LEFT)
        self._inst_filter = tk.StringVar()
        self._inst_filter.trace_add('write', lambda *_: self._refresh_inst_list())
        ttk.Entry(iff, textvariable=self._inst_filter).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4,0))
        ilb_f = ttk.Frame(ip); ilb_f.pack(fill=tk.BOTH, expand=True)
        self._inst_lb = tk.Listbox(ilb_f, selectmode=tk.EXTENDED, width=24,
                                    exportselection=False, font=('Courier', 9))
        isb = ttk.Scrollbar(ilb_f, orient=tk.VERTICAL, command=self._inst_lb.yview)
        self._inst_lb.configure(yscrollcommand=isb.set)
        self._inst_lb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True); isb.pack(side=tk.LEFT, fill=tk.Y)
        self._inst_lb.bind('<<ListboxSelect>>', self._on_inst_select)
        grp_bf = ttk.Frame(ip); grp_bf.pack(fill=tk.X, pady=(4,0))
        ttk.Button(grp_bf, text='+ grp',  command=self._add_insts_to_group).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(grp_bf, text='− grp',  command=self._rm_insts_from_group).pack(side=tk.LEFT, fill=tk.X, expand=True)

        # ── Column 2: Groups (full height) ────────────────────────────────────
        gp = ttk.LabelFrame(main, text='Groups', padding=4, width=220)
        gp.pack(side=tk.LEFT, fill=tk.Y, padx=(0,3))
        gp.pack_propagate(False)

        # Scrollable groups list — fills all available vertical space
        glist_f = ttk.Frame(gp); glist_f.pack(fill=tk.BOTH, expand=True)
        self._grp_canvas = tk.Canvas(glist_f, highlightthickness=0, bg='#f8f8f8')
        gscroll = ttk.Scrollbar(glist_f, orient=tk.VERTICAL, command=self._grp_canvas.yview)
        self._grp_canvas.configure(yscrollcommand=gscroll.set)
        self._grp_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        gscroll.pack(side=tk.LEFT, fill=tk.Y)
        self._grp_inner = ttk.Frame(self._grp_canvas)
        self._grp_canvas.create_window((0,0), window=self._grp_inner, anchor='nw')
        self._grp_inner.bind('<Configure>',
            lambda e: self._grp_canvas.configure(scrollregion=self._grp_canvas.bbox('all')))
        # Mouse-wheel scrolling on the groups canvas
        self._grp_canvas.bind('<Enter>',
            lambda _: self._grp_canvas.bind_all('<MouseWheel>', self._grp_scroll))
        self._grp_canvas.bind('<Leave>',
            lambda _: self._grp_canvas.unbind_all('<MouseWheel>'))

        bf = ttk.Frame(gp); bf.pack(side=tk.BOTTOM, fill=tk.X, pady=(3,0))
        ttk.Button(bf, text='Utilities…', command=self._open_utilities).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(bf, text='Save',       command=self._save_groups).pack(side=tk.LEFT, fill=tk.X, expand=True)
        bf2 = ttk.Frame(gp); bf2.pack(side=tk.BOTTOM, fill=tk.X, pady=(2,0))
        ttk.Button(bf2, text='+ From selection', command=self._new_group_from_sel).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(bf2, text='Add sub-group',    command=self._add_sub_group).pack(side=tk.LEFT, fill=tk.X, expand=True)

        # ── Canvas ────────────────────────────────────────────────────────────
        cv_f = ttk.Frame(main); cv_f.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._fig = Figure(figsize=(9,7), facecolor='#ececec')
        self._ax  = self._fig.add_subplot(111)
        self._canvas = FigureCanvasTkAgg(self._fig, master=cv_f)
        NavigationToolbar2Tk(self._canvas, cv_f).update()
        self._canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        self._canvas.mpl_connect('button_press_event', self._on_canvas_click)
        self._canvas.mpl_connect('motion_notify_event', self._on_hover)

        root.bind('<f>', lambda e: root.attributes('-fullscreen', not root.attributes('-fullscreen')))
        root.bind('<Escape>', lambda e: root.attributes('-fullscreen', False))
        root.bind('<q>', lambda e: root.destroy())

    # ── File ops ──────────────────────────────────────────────────────────────

    @staticmethod
    def _find_lef(def_path: str) -> str:
        """Return a LEF path for the given DEF, or '' if none found."""
        stem = re.sub(r'\.def$', '', def_path, flags=re.IGNORECASE)
        for c in [stem + '.lef', stem.replace('.input', '') + '.lef']:
            if os.path.exists(c):
                return c
        d = os.path.dirname(def_path)
        lefs = [f for f in sorted(os.listdir(d)) if f.lower().endswith('.lef')]
        return os.path.join(d, lefs[0]) if len(lefs) == 1 else ''

    def _browse_def(self):
        p = filedialog.askopenfilename(
            filetypes=[('DEF / BDB', '*.def *.bdb'), ('All', '*')])
        if not p: return
        self._def_var.set(p)
        if p.lower().endswith('.bdb'):
            self._lef_var.set('')   # LEF not needed for direct BDB load
            return
        lef = self._find_lef(p)
        if lef: self._lef_var.set(lef)

    def _browse_lef(self):
        p = filedialog.askopenfilename(filetypes=[('LEF', '*.lef'), ('All', '*')])
        if p: self._lef_var.set(p)

    def _load(self):
        def_p = self._def_var.get().strip()
        lef_p = self._lef_var.get().strip()
        if not def_p or not os.path.exists(def_p):
            self._status.set('DEF/BDB not found.'); return
        # Direct BDB load — no LEF required
        if def_p.lower().endswith('.bdb'):
            self._status.set('Loading BDB…'); self.root.update_idletasks()
            try: msg = self.data.load_bdb(def_p)
            except Exception as e: self._status.set(f'Error: {e}'); return
        else:
            # Auto-find LEF if field is empty
            if not lef_p:
                lef_p = self._find_lef(def_p)
                if lef_p: self._lef_var.set(lef_p)
            if not lef_p or not os.path.exists(lef_p):
                self._status.set('LEF not found.'); return
            self._status.set('Parsing…'); self.root.update_idletasks()
            try: msg = self.data.load(def_p, lef_p)
            except Exception as e: self._status.set(f'Error: {e}'); return
        self.selected_nets.clear(); self._sel_gid = None; self._hl_active = False
        self._refresh_net_list(); self._inst_items = []; self._inst_lb.delete(0, tk.END)
        depths = self.data.available_depths()
        if depths:
            self._depth_sb.configure(from_=min(depths), to=max(depths))
            self._depth_var.set(max(depths))
        self._rebuild_grp_panel(); self._draw_canvas()
        self._status.set(f'Loaded: {msg}')
        if self._utils_win and self._utils_win.winfo_exists():
            self._hpwl_refresh()
            self._common_nets_refresh()
        if self._pending_ipc_msg is not None:
            pending, self._pending_ipc_msg = self._pending_ipc_msg, None
            self._on_ipc_message(pending)

    def _save_groups(self):
        self.data.save_groups()
        self._status.set(f'Groups saved → {GroupTree.sidecar_path(self.data.def_path)}')

    # ── Net / inst ────────────────────────────────────────────────────────────

    def _refresh_net_list(self):
        filt = self._net_filter.get().lower()
        self._net_items = [n for n in self.data.all_nets if filt in n.lower()]
        self._net_lb.delete(0, tk.END)
        for n in self._net_items: self._net_lb.insert(tk.END, n)
        self._updating = True
        try:
            for i, n in enumerate(self._net_items):
                if n in self.selected_nets: self._net_lb.selection_set(i)
        finally: self._updating = False

    def _on_net_select(self, _=None):
        if self._updating: return
        self.selected_nets = {self._net_items[i] for i in self._net_lb.curselection()
                              if i < len(self._net_items)}
        self._hl_active = False
        self._refresh_inst_list()

    def _clear_selection(self):
        self.selected_nets.clear(); self._net_lb.selection_clear(0, tk.END)
        self._inst_items = []; self._inst_lb.delete(0, tk.END)
        self._hl_active = False; self._draw_canvas()
        if getattr(self, '_ipc', None):
            self._ipc.send({'type': 'clear'})

    def _refresh_inst_list(self, override_list=None):
        """Populate instances panel. override_list bypasses net-based visibility."""
        filt = self._inst_filter.get().lower()
        if override_list is not None:
            base = sorted(override_list)
        else:
            base = self.data.visible_insts(self.selected_nets)
        self._inst_items = [i for i in base if filt in i.lower()]
        self._inst_lb.delete(0, tk.END)
        for n in self._inst_items:
            self._inst_lb.insert(tk.END, f'{n}  ({self.data.inst_info.get(n,{}).get("cell","?")})')

    def _on_inst_select(self, _=None):
        if self._updating: return
        sel = {self._inst_items[i] for i in self._inst_lb.curselection() if i < len(self._inst_items)}
        if not sel: return
        added = set()
        for inst in sel: added |= self.data.inst_nets.get(inst, set())
        new = added - self.selected_nets
        if not new: return
        self.selected_nets |= new
        self._updating = True
        try:
            for i, n in enumerate(self._net_items):
                if n in self.selected_nets: self._net_lb.selection_set(i)
        finally: self._updating = False
        self._hl_active = True
        self._refresh_inst_list(); self._draw_canvas()
        if getattr(self, '_ipc', None) and sel:
            self._ipc.send({'type': 'select_inst', 'inst_names': sorted(sel)})

    # ── IPC ───────────────────────────────────────────────────────────────────

    def _on_ipc_message(self, msg: dict):
        kind = msg.get('type')
        if kind == 'select_bundle':
            all_nets = set(getattr(self.data, 'all_nets', []))
            if not all_nets:
                self._pending_ipc_msg = msg
                return
            net_names = [n for n in msg.get('net_names', []) if n in all_nets]
            if not net_names:
                return
            self.selected_nets = set(net_names)
            self._updating = True
            try:
                self._net_lb.selection_clear(0, tk.END)
                for i, n in enumerate(self._net_items):
                    if n in self.selected_nets:
                        self._net_lb.selection_set(i)
            finally:
                self._updating = False
            self._hl_active = True
            self._refresh_inst_list(); self._draw_canvas()
        elif kind == 'clear':
            if self.selected_nets:
                self._clear_selection()

    # ── Groups panel ─────────────────────────────────────────────────────────

    def _grp_scroll(self, event):
        self._grp_canvas.yview_scroll(int(-1 * (event.delta / 120)), 'units')

    def _rebuild_grp_panel(self):
        """Rebuild the scrollable groups list from scratch."""
        for w in self._grp_inner.winfo_children(): w.destroy()
        self._grp_vis = {}
        for g, depth in self.data.groups.walk():
            vis_var = tk.BooleanVar(value=True)
            self._grp_vis[g.id] = vis_var
            row = ttk.Frame(self._grp_inner)
            row.pack(fill=tk.X, pady=1)
            indent = depth * 14
            if indent: ttk.Frame(row, width=indent).pack(side=tk.LEFT)
            # Color swatch
            swatch = tk.Label(row, bg=g.color, width=2, relief='flat')
            swatch.pack(side=tk.LEFT, padx=(0,2))
            # Visibility checkbox
            ttk.Checkbutton(row, variable=vis_var,
                            command=self._draw_canvas).pack(side=tk.LEFT)
            # Name label — click to select
            n = len(self.data.groups.all_insts(g.id))
            lbl = tk.Label(row, text=f'{g.name} ({n})', anchor='w',
                           fg=g.color, bg='#f8f8f8',
                           font=('TkDefaultFont', 9, 'bold' if g.id == self._sel_gid else 'normal'),
                           cursor='hand2')
            lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)
            lbl.bind('<Button-1>', lambda e, gid=g.id: self._select_group(gid))
            # Delete button
            tk.Label(row, text='×', fg='#999', bg='#f8f8f8',
                     cursor='hand2').pack(side=tk.RIGHT, padx=(0,2))
            row.winfo_children()[-1].bind(
                '<Button-1>', lambda e, gid=g.id: self._delete_group(gid))

    def _select_group(self, gid):
        self._sel_gid = gid
        g = self.data.groups.get(gid)
        if g:
            # Show group's instances in the inst panel
            insts = self.data.groups.all_insts(gid)
            self._refresh_inst_list(override_list=insts)
        self._rebuild_grp_panel(); self._draw_canvas()
        self._status.set(f'Group "{self.data.groups.get(gid).name}" selected — '
                         f'{len(self.data.groups.all_insts(gid))} instance(s).')

    def _new_group_from_sel(self):
        """Create a new group from currently selected instances and/or nets."""
        insts = {self._inst_items[i] for i in self._inst_lb.curselection() if i < len(self._inst_items)}
        nets  = {self._net_items[i]  for i in self._net_lb.curselection()  if i < len(self._net_items)}
        if not insts and not nets:
            self._status.set('Select instances or nets first.'); return
        name = simpledialog.askstring('New Group', 'Group name:')
        if not name: return
        g = self.data.groups.new_group(name)
        for inst in insts: self.data.groups.add_member(g.id, 'inst', inst)
        for net  in nets:  self.data.groups.add_member(g.id, 'net',  net)
        self._sel_gid = g.id
        self._rebuild_grp_panel(); self._draw_canvas()
        self._status.set(f'Created group "{name}" with {len(insts)} inst(s), {len(nets)} net(s).')

    def _add_sub_group(self):
        if not self._sel_gid:
            self._status.set('Select a group first.'); return
        name = simpledialog.askstring('New Sub-group', 'Sub-group name:')
        if not name: return
        g = self.data.groups.new_group(name, parent_id=self._sel_gid)
        self._rebuild_grp_panel(); self._draw_canvas()
        self._status.set(f'Created sub-group "{name}" under "{self.data.groups.get(self._sel_gid).name}".')

    def _delete_group(self, gid):
        self.data.groups.delete_group(gid)
        if self._sel_gid == gid: self._sel_gid = None
        self._rebuild_grp_panel(); self._draw_canvas()

    def _add_insts_to_group(self):
        if not self._sel_gid:
            self._status.set('Select a group in the Groups panel first.'); return
        insts = {self._inst_items[i] for i in self._inst_lb.curselection() if i < len(self._inst_items)}
        nets  = {self._net_items[i]  for i in self._net_lb.curselection()  if i < len(self._net_items)}
        if not insts and not nets:
            self._status.set('Select instances or nets to add.'); return
        for inst in insts: self.data.groups.add_member(self._sel_gid, 'inst', inst)
        for net  in nets:  self.data.groups.add_member(self._sel_gid, 'net',  net)
        self._rebuild_grp_panel(); self._draw_canvas()
        gname = self.data.groups.get(self._sel_gid).name
        self._status.set(f'Added {len(insts)} inst(s), {len(nets)} net(s) to "{gname}".')

    def _rm_insts_from_group(self):
        if not self._sel_gid: return
        sel_insts = {self._inst_items[i] for i in self._inst_lb.curselection() if i < len(self._inst_items)}
        for inst in sel_insts: self.data.groups.remove_member(self._sel_gid, 'inst', inst)
        insts = self.data.groups.all_insts(self._sel_gid)
        self._refresh_inst_list(override_list=insts)
        self._rebuild_grp_panel(); self._draw_canvas()

    # ── Canvas ────────────────────────────────────────────────────────────────

    def _highlight_nets(self):
        if not self.selected_nets:
            self._status.set('Select nets first.'); return
        self._hl_active = True
        self._draw_canvas()

    def _draw_canvas(self):
        ax = self._ax; ax.clear()
        self._patch_inst.clear(); self._patch_group.clear(); self._hover_ann = None
        draw_die(ax, self.data.die)

        # Background: all instances at the selected depth
        depth = self._depth_var.get()
        depth_insts = {k: v for k, v in self.data.inst_info.items() if v.get('depth') == depth}
        if depth_insts:
            draw_bg_instances(ax, depth_insts)

        # Colored highlight: net-based, only when _hl_active or a group is selected
        vis = set(self.data.visible_insts(self.selected_nets)) if self._hl_active else set()
        inst_highlight = None
        if self._sel_gid:
            direct, net_reached = self.data.group_highlight(self._sel_gid)
            vis |= direct | net_reached
            g = self.data.groups.get(self._sel_gid)
            if g:
                inst_highlight = {}
                for inst in direct:
                    inst_highlight[inst] = g.color
                for inst in net_reached:
                    if inst not in inst_highlight:
                        inst_highlight[inst] = lighten_color(g.color)
        self._patch_inst = draw_selected_instances(ax, sorted(vis), self.data, self.selected_nets,
                                                    inst_highlight=inst_highlight)

        # Only draw visible groups
        visible_gids = {gid for gid, var in self._grp_vis.items() if var.get()}
        self._patch_group = _draw_group_boxes_filtered(
            ax, self.data.groups, self.data.inst_info,
            visible_gids, highlight_id=self._sel_gid)

        fit_view(ax, sorted(vis), self.data.inst_info, self.data.die, full_die=not vis)
        ax.set_aspect('equal')
        parts = [f'depth={depth}: {len(depth_insts)} bg inst(s)']
        if self._hl_active and vis:
            parts.append(f'{len(vis)} highlighted')
        if self.data.groups:
            parts.append(f'{len(self.data.groups)} group(s)')
        ax.set_title('  ·  '.join(parts), fontsize=11)
        self._canvas.draw_idle()

    def _on_canvas_click(self, event):
        if event.inaxes != self._ax: return
        if self._rect_active: return   # RectangleSelector owns these events
        tb = getattr(self._canvas, 'toolbar', None)
        if tb and getattr(tb, 'mode', '') != '': return
        for patch, gid in list(self._patch_group.items()):
            if patch.contains(event)[0]:
                self._select_group(gid); return
        for patch, inst in list(self._patch_inst.items()):
            if patch.contains(event)[0]:
                if inst in self._inst_items:
                    idx = self._inst_items.index(inst)
                    self._inst_lb.selection_clear(0, tk.END)
                    self._inst_lb.selection_set(idx); self._inst_lb.see(idx)
                self._on_inst_select(); return

    def _on_hover(self, event):
        if event.inaxes != self._ax: return
        for patch, inst in list(self._patch_inst.items()):
            if patch.contains(event)[0]:
                info = self.data.inst_info.get(inst, {})
                nets = sorted(self.data.inst_nets.get(inst, set()))
                grps = [g.name for g in self.data.groups.groups_containing_inst(inst)]
                tip  = f'{inst}\n{info.get("cell","")}'
                if nets: tip += f'\nnets: {", ".join(nets[:3])}{"…" if len(nets)>3 else ""}'
                if grps: tip += f'\ngroups: {", ".join(grps)}'
                self._set_hover(event, tip); return
        self._clear_hover()

    def _set_hover(self, event, text):
        self._clear_hover()
        self._hover_ann = self._ax.annotate(
            text, xy=(event.xdata, event.ydata),
            xytext=(12,12), textcoords='offset points', fontsize=7.5,
            bbox=dict(boxstyle='round,pad=0.4', fc='lightyellow', ec='#aaaaaa', alpha=0.95),
            zorder=30)
        self._canvas.draw_idle()

    def _clear_hover(self):
        if self._hover_ann:
            try: self._hover_ann.remove()
            except Exception: pass
            self._hover_ann = None; self._canvas.draw_idle()


    # ── Group Utilities panel ─────────────────────────────────────────────────

    def _open_utilities(self):
        if self._utils_win and self._utils_win.winfo_exists():
            self._utils_win.lift(); return
        win = tk.Toplevel(self.root)
        win.title('Group Utilities'); win.geometry('500x360')
        win.resizable(False, False); self._utils_win = win
        win.transient(self.root)

        nb = ttk.Notebook(win); nb.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

        hf = ttk.Frame(nb); nb.add(hf, text='  HPWL Range  ')
        self._build_hpwl_tab(hf)

        rf = ttk.Frame(nb); nb.add(rf, text='  Rect Select  ')
        self._build_rect_tab(rf)

        cf = ttk.Frame(nb); nb.add(cf, text='  Common Nets  ')
        self._build_common_nets_tab(cf)

        nb.bind('<<NotebookTabChanged>>',
                lambda e: self._on_utils_tab(nb.index('current')))
        win.protocol('WM_DELETE_WINDOW', self._on_utils_close)

        if self.data.all_nets:
            self._hpwl_refresh()
            self._common_nets_refresh()

    def _on_utils_close(self):
        self._rect_deactivate()
        if self._utils_win:
            self._utils_win.destroy()
            self._utils_win = None

    def _on_utils_tab(self, idx):
        if idx != 1:   # leaving rect tab → deactivate rect mode
            self._rect_deactivate()
        if idx == 2:   # entering common nets tab → refresh group lists
            self._common_nets_refresh()

    # ── HPWL Range tab ───────────────────────────────────────────────────────

    def _build_hpwl_tab(self, parent):
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

        fig = Figure(figsize=(4.8, 1.5), facecolor='#f0f0f0')
        ax = fig.add_subplot(111); ax.set_facecolor('#f8f8f8')
        fig.subplots_adjust(left=0.07, right=0.97, bottom=0.22, top=0.90)
        ax.set_xlabel('HPWL (µm)', fontsize=7); ax.tick_params(labelsize=7)
        fc = FigureCanvasTkAgg(fig, master=parent)
        fc.get_tk_widget().pack(fill=tk.X, padx=4, pady=(4, 2))
        self._hpwl_fig = fig; self._hpwl_ax = ax; self._hpwl_fc = fc
        self._hpwl_lo_line = self._hpwl_hi_line = None

        sf = ttk.Frame(parent); sf.pack(fill=tk.X, padx=10, pady=2)
        sf.columnconfigure(1, weight=1)
        ttk.Label(sf, text='Lo (µm):').grid(row=0, column=0, sticky='w', pady=1)
        self._hpwl_lo = tk.DoubleVar(value=0.0)
        self._hpwl_lo_sc = tk.Scale(sf, variable=self._hpwl_lo, orient=tk.HORIZONTAL,
                                     from_=0, to=200, resolution=0.5,
                                     command=lambda _: self._hpwl_update())
        self._hpwl_lo_sc.grid(row=0, column=1, sticky='ew', padx=4)
        ttk.Label(sf, text='Hi (µm):').grid(row=1, column=0, sticky='w', pady=1)
        self._hpwl_hi = tk.DoubleVar(value=200.0)
        self._hpwl_hi_sc = tk.Scale(sf, variable=self._hpwl_hi, orient=tk.HORIZONTAL,
                                     from_=0, to=200, resolution=0.5,
                                     command=lambda _: self._hpwl_update())
        self._hpwl_hi_sc.grid(row=1, column=1, sticky='ew', padx=4)

        self._hpwl_count = tk.StringVar(value='—')
        ttk.Label(parent, textvariable=self._hpwl_count,
                  anchor='center', font=('TkDefaultFont', 9, 'italic')).pack(pady=2)
        ttk.Button(parent, text='Create Net Group', command=self._hpwl_create).pack(
            fill=tk.X, padx=80, pady=(0, 6))

    def _hpwl_refresh(self):
        """Redraw histogram and set slider range from current data."""
        dist = self.data.hpwl_distribution()
        if not dist:
            return
        vals = [v for _, v in dist]
        max_h = max(vals)
        for sc in (self._hpwl_lo_sc, self._hpwl_hi_sc):
            sc.configure(to=max_h)
        self._hpwl_hi.set(max_h)

        ax = self._hpwl_ax; ax.clear()
        ax.hist(vals, bins=40, color='#4477cc', alpha=0.75, linewidth=0)
        ax.set_xlabel('HPWL (µm)', fontsize=7); ax.tick_params(labelsize=7)
        ax.set_facecolor('#f8f8f8')
        lo, hi = self._hpwl_lo.get(), self._hpwl_hi.get()
        self._hpwl_lo_line = ax.axvline(lo, color='#e66', lw=1.2, ls='--')
        self._hpwl_hi_line = ax.axvline(hi, color='#c33', lw=1.5)
        self._hpwl_fc.draw_idle()
        self._hpwl_update()

    def _hpwl_update(self):
        lo, hi = self._hpwl_lo.get(), self._hpwl_hi.get()
        if lo > hi:
            self._hpwl_lo.set(hi); lo = hi
        # update lines
        if self._hpwl_lo_line:
            self._hpwl_lo_line.set_xdata([lo, lo])
            self._hpwl_hi_line.set_xdata([hi, hi])
            self._hpwl_fc.draw_idle()
        # count matching nets
        if self.data.bdb is not None:
            n = len(self.data.bdb.nets_by_hpwl(lo, hi))
        else:
            n = sum(1 for net in self.data.all_nets
                    if lo <= self.data._net_hpwl(net) <= hi)
        self._hpwl_count.set(f'{n} net(s) in [{lo:.1f}, {hi:.1f}] µm')

    def _hpwl_create(self):
        lo, hi = self._hpwl_lo.get(), self._hpwl_hi.get()
        g = self.data.group_nets(lo, hi)
        if g is None:
            self._status.set('No nets in that HPWL range.'); return
        self._sel_gid = g.id
        self._rebuild_grp_panel(); self._draw_canvas()
        self._status.set(f'Created group "{g.name}".')

    # ── Rect Select tab ───────────────────────────────────────────────────────

    def _build_rect_tab(self, parent):
        ttk.Label(parent, text=(
            'Click and drag on the canvas to select\n'
            'instances in a rectangular region.\n\n'
            'Each release creates a new inst group.\n'
            'Click the button again to deactivate.'
        ), justify=tk.CENTER, font=('TkDefaultFont', 10)).pack(pady=20)
        self._rect_btn_var = tk.StringVar(value='Activate Rect Selection')
        self._rect_btn = ttk.Button(parent, textvariable=self._rect_btn_var,
                                    command=self._rect_toggle)
        self._rect_btn.pack(fill=tk.X, padx=60, pady=4)
        self._rect_status = tk.StringVar(value='')
        ttk.Label(parent, textvariable=self._rect_status,
                  anchor='center', foreground='#336633').pack(pady=4)

    def _rect_toggle(self):
        if self._rect_active:
            self._rect_deactivate()
        else:
            self._rect_activate()

    def _rect_activate(self):
        from matplotlib.widgets import RectangleSelector
        # Clear any toolbar zoom/pan mode
        tb = getattr(self._canvas, 'toolbar', None)
        if tb:
            try: tb.mode = ''
            except Exception: pass
        self._rect_selector = RectangleSelector(
            self._ax, self._on_rect_selected,
            useblit=True, button=[1],
            minspanx=0.5, minspany=0.5,
            spancoords='data', interactive=False)
        self._rect_selector.set_active(True)
        self._rect_active = True
        self._rect_btn_var.set('Deactivate Rect Selection')
        self._rect_status.set('Active — drag on canvas')
        self._status.set('Rect mode: drag on the canvas to group instances.')

    def _rect_deactivate(self):
        if self._rect_selector is not None:
            try: self._rect_selector.set_active(False)
            except Exception: pass
            self._rect_selector = None
        self._rect_active = False
        if hasattr(self, '_rect_btn_var'):
            self._rect_btn_var.set('Activate Rect Selection')
        if hasattr(self, '_rect_status'):
            self._rect_status.set('')
        self._status.set('Rect mode deactivated.')

    def _on_rect_selected(self, eclick, erelease):
        xl = min(eclick.xdata, erelease.xdata)
        xh = max(eclick.xdata, erelease.xdata)
        yl = min(eclick.ydata, erelease.ydata)
        yh = max(eclick.ydata, erelease.ydata)
        g = self.data.group_insts(xl, yl, xh, yh)
        if g is None:
            self._rect_status.set('No instances in rect — try again.')
            return
        self._sel_gid = g.id
        self._rebuild_grp_panel(); self._draw_canvas()
        self._rect_status.set(f'Created "{g.name}".')
        self._status.set(f'Created group "{g.name}" from rect selection.')

    # ── Common Nets tab ───────────────────────────────────────────────────────

    def _build_common_nets_tab(self, parent):
        ttk.Label(parent, text=(
            'Find nets connecting instances in two groups.\n'
            'Select one inst-group for each endpoint.'
        ), justify=tk.CENTER).pack(pady=(12, 6))

        gf = ttk.Frame(parent); gf.pack(fill=tk.X, padx=20, pady=4)
        ttk.Label(gf, text='Group 1:', width=9).grid(row=0, column=0, sticky='w', pady=3)
        self._cn_g1 = tk.StringVar()
        self._cn_cb1 = ttk.Combobox(gf, textvariable=self._cn_g1,
                                     state='readonly', width=28)
        self._cn_cb1.grid(row=0, column=1, padx=4)
        self._cn_cb1.bind('<<ComboboxSelected>>', lambda _: self._common_nets_update())

        ttk.Label(gf, text='Group 2:', width=9).grid(row=1, column=0, sticky='w', pady=3)
        self._cn_g2 = tk.StringVar()
        self._cn_cb2 = ttk.Combobox(gf, textvariable=self._cn_g2,
                                     state='readonly', width=28)
        self._cn_cb2.grid(row=1, column=1, padx=4)
        self._cn_cb2.bind('<<ComboboxSelected>>', lambda _: self._common_nets_update())

        self._cn_count = tk.StringVar(value='—')
        ttk.Label(parent, textvariable=self._cn_count,
                  anchor='center', font=('TkDefaultFont', 9, 'italic')).pack(pady=4)
        ttk.Button(parent, text='Create Net Group',
                   command=self._common_nets_create).pack(fill=tk.X, padx=80, pady=(0, 6))

        # Refresh dropdowns when tab is shown
        self._cn_gid_map: dict[str, str] = {}   # display label → gid

    def _common_nets_refresh(self):
        """Rebuild the two group comboboxes from current groups."""
        choices = []
        gid_map = {}
        for g, _ in self.data.groups.walk():
            insts = self.data.groups.all_insts(g.id)
            if not insts:
                continue
            label = f'{g.name} ({len(insts)} insts)'
            choices.append(label)
            gid_map[label] = g.id
        self._cn_gid_map = gid_map
        for cb in (self._cn_cb1, self._cn_cb2):
            cb['values'] = choices
        self._cn_count.set('—')

    def _common_nets_update(self):
        g1_label = self._cn_g1.get(); g2_label = self._cn_g2.get()
        gid1 = self._cn_gid_map.get(g1_label)
        gid2 = self._cn_gid_map.get(g2_label)
        if not gid1 or not gid2 or gid1 == gid2:
            self._cn_count.set('—'); return
        insts1 = self.data.groups.all_insts(gid1)
        insts2 = self.data.groups.all_insts(gid2)
        n = sum(1 for ni in self.data.net_insts.values()
                if ni & insts1 and ni & insts2)
        self._cn_count.set(f'{n} common net(s) found')

    def _common_nets_create(self):
        g1_label = self._cn_g1.get(); g2_label = self._cn_g2.get()
        gid1 = self._cn_gid_map.get(g1_label)
        gid2 = self._cn_gid_map.get(g2_label)
        if not gid1 or not gid2:
            self._status.set('Select both groups first.'); return
        if gid1 == gid2:
            self._status.set('Groups must be different.'); return
        g = self.data.group_common_nets(gid1, gid2)
        if g is None:
            self._status.set('No common nets found between those groups.'); return
        self._sel_gid = g.id
        self._rebuild_grp_panel(); self._draw_canvas()
        self._status.set(f'Created group "{g.name}".')


def _draw_group_boxes_filtered(ax, groups, inst_info, visible_gids, highlight_id=None):
    """Like draw_group_boxes but skips groups not in visible_gids."""
    import matplotlib.patches as mp
    patch_group = {}

    def _draw(g, depth):
        if g.id not in visible_gids:
            return
        insts  = groups.all_insts(g.id)
        placed = [inst_info[i] for i in insts if i in inst_info]
        if not placed: return
        x1 = min(p['x1'] for p in placed); y1 = min(p['y1'] for p in placed)
        x2 = max(p['x2'] for p in placed); y2 = max(p['y2'] for p in placed)
        pad  = depth * 0.3
        lw   = 2.8 if g.id == highlight_id else 1.6
        ls   = '-' if g.id == highlight_id else '--'
        falpha = max(0.10 - depth * 0.025, 0.03)
        patch = mp.Rectangle(
            (x1-pad, y1-pad), (x2-x1)+2*pad, (y2-y1)+2*pad,
            linewidth=lw, edgecolor=g.color, facecolor=g.color,
            linestyle=ls, alpha=falpha, zorder=4+depth, picker=True)
        ax.add_patch(patch)
        patch_group[patch] = g.id
        ax.text(x1-pad, y2+pad, g.name,
                fontsize=8, color=g.color, fontweight='bold',
                va='bottom', clip_on=True, zorder=5+depth)
        for child in groups.children(g.id):
            _draw(child, depth+1)

    for root_g in groups.roots(): _draw(root_g, 0)
    return patch_group


def main():
    args = sys.argv[1:]
    ipc_name = None
    if '--ipc' in args:
        idx = args.index('--ipc')
        if idx + 1 < len(args):
            ipc_name = args[idx + 1]; args = args[:idx] + args[idx + 2:]
        else:
            args = args[:idx]

    root = tk.Tk()
    try: root.tk.call('tk', 'scaling', 2.0)
    except Exception: pass
    app = DefVizV3(root)
    if len(args) >= 1:
        def_abs = os.path.abspath(args[0])
        app._def_var.set(def_abs)
        if len(args) >= 2:
            app._lef_var.set(os.path.abspath(args[1]))
        elif not def_abs.lower().endswith('.bdb'):
            lef = DefVizV3._find_lef(def_abs)
            if lef: app._lef_var.set(lef)
    if len(args) >= 1 and os.path.exists(args[0]):
        root.after(200, app._load)
        if ipc_name is None:
            ipc_name = os.path.splitext(os.path.basename(args[0]))[0]

    if ipc_name:
        from viz_ipc import VizIPC, POLL_MS
        ipc = VizIPC(ipc_name); ipc.on_message = app._on_ipc_message
        ipc.connect_or_serve(); app._ipc = ipc
        def _tick(): ipc.poll(); root.after(POLL_MS, _tick)
        root.after(POLL_MS, _tick)

    root.lift(); root.focus_force()
    root.mainloop()

if __name__ == '__main__':
    main()
