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
from def_viz_shared import (DefVizData, GroupStore,
                             draw_die, draw_bg_instances,
                             draw_selected_instances, draw_group_boxes, fit_view)


class DefVizV3:
    def __init__(self, root):
        self.root  = root
        root.title('DEF Viz — V3: Canvas-first Groups')
        root.geometry('1480x860')

        self.data          = DefVizData()
        self.selected_nets = set()
        self._net_items    = []
        self._inst_items   = []
        self._patch_inst   = {}
        self._patch_group  = {}
        self._hover_ann    = None
        self._updating     = False
        self._show_all_bg  = tk.BooleanVar(value=False)

        # {gid: BooleanVar} — per-group visibility
        self._grp_vis:  dict[str, tk.BooleanVar] = {}
        self._sel_gid:  str = None   # currently selected group
        self._canvas_sel_insts: set = set()  # insts rubber-banded on canvas

        self._build_ui()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = self.root

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

        main = ttk.Frame(root); main.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=4, pady=2)

        # ── Left column: Nets + Groups ─────────────────────────────────────────
        left = ttk.Frame(main, width=210); left.pack(side=tk.LEFT, fill=tk.Y, padx=(0,4))
        left.pack_propagate(False)

        np = ttk.LabelFrame(left, text='Nets', padding=4)
        np.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        nf = ttk.Frame(np); nf.pack(fill=tk.X, pady=(0,2))
        ttk.Label(nf, text='Filter:').pack(side=tk.LEFT)
        self._net_filter = tk.StringVar()
        self._net_filter.trace_add('write', lambda *_: self._refresh_net_list())
        ttk.Entry(nf, textvariable=self._net_filter).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4,0))
        nlb_f = ttk.Frame(np); nlb_f.pack(fill=tk.BOTH, expand=True)
        self._net_lb = tk.Listbox(nlb_f, selectmode=tk.EXTENDED, width=24,
                                   exportselection=False, font=('Courier', 9))
        nsb = ttk.Scrollbar(nlb_f, orient=tk.VERTICAL, command=self._net_lb.yview)
        self._net_lb.configure(yscrollcommand=nsb.set); self._net_lb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True); nsb.pack(side=tk.LEFT, fill=tk.Y)
        self._net_lb.bind('<<ListboxSelect>>', self._on_net_select)
        ttk.Button(np, text='Clear selection', command=self._clear_selection).pack(fill=tk.X, pady=(4,0))
        ttk.Checkbutton(np, text='Show all instances',
                        variable=self._show_all_bg, command=self._draw_canvas).pack(anchor='w', pady=(2,0))

        # Groups sub-panel (fixed height at bottom of left column)
        gp = ttk.LabelFrame(left, text='Groups', padding=4)
        gp.pack(side=tk.BOTTOM, fill=tk.X)

        # Scrollable groups list
        glist_f = ttk.Frame(gp); glist_f.pack(fill=tk.BOTH, expand=True)
        self._grp_canvas = tk.Canvas(glist_f, height=160, highlightthickness=0,
                                      bg='#f8f8f8')
        gscroll = ttk.Scrollbar(glist_f, orient=tk.VERTICAL,
                                 command=self._grp_canvas.yview)
        self._grp_canvas.configure(yscrollcommand=gscroll.set)
        self._grp_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        gscroll.pack(side=tk.LEFT, fill=tk.Y)
        self._grp_inner = ttk.Frame(self._grp_canvas)
        self._grp_canvas.create_window((0,0), window=self._grp_inner, anchor='nw')
        self._grp_inner.bind('<Configure>',
            lambda e: self._grp_canvas.configure(scrollregion=self._grp_canvas.bbox('all')))

        bf = ttk.Frame(gp); bf.pack(fill=tk.X, pady=(3,0))
        ttk.Button(bf, text='+ From selection',  command=self._new_group_from_sel).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(bf, text='Add sub-group',      command=self._add_sub_group).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(bf, text='Save',               command=self._save_groups).pack(fill=tk.X, pady=(2,0))

        # ── Inst panel ────────────────────────────────────────────────────────
        ip = ttk.LabelFrame(main, text='Instances', padding=4, width=215)
        ip.pack(side=tk.LEFT, fill=tk.Y, padx=(0,4)); ip.pack_propagate(False)
        iff = ttk.Frame(ip); iff.pack(fill=tk.X, pady=(0,2))
        ttk.Label(iff, text='Filter:').pack(side=tk.LEFT)
        self._inst_filter = tk.StringVar()
        self._inst_filter.trace_add('write', lambda *_: self._refresh_inst_list())
        ttk.Entry(iff, textvariable=self._inst_filter).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4,0))
        ilb_f = ttk.Frame(ip); ilb_f.pack(fill=tk.BOTH, expand=True)
        self._inst_lb = tk.Listbox(ilb_f, selectmode=tk.EXTENDED, width=25,
                                    exportselection=False, font=('Courier', 9))
        isb = ttk.Scrollbar(ilb_f, orient=tk.VERTICAL, command=self._inst_lb.yview)
        self._inst_lb.configure(yscrollcommand=isb.set); self._inst_lb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True); isb.pack(side=tk.LEFT, fill=tk.Y)
        self._inst_lb.bind('<<ListboxSelect>>', self._on_inst_select)
        # Add/remove from selected group
        grp_bf = ttk.Frame(ip); grp_bf.pack(fill=tk.X, pady=(4,0))
        ttk.Button(grp_bf, text='Add to group',    command=self._add_insts_to_group).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(grp_bf, text='Rm from group',   command=self._rm_insts_from_group).pack(side=tk.LEFT, fill=tk.X, expand=True)

        # ── Canvas ────────────────────────────────────────────────────────────
        cv_f = ttk.Frame(main); cv_f.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._fig = Figure(figsize=(9,7), facecolor='#ececec')
        self._ax  = self._fig.add_subplot(111)
        self._canvas = FigureCanvasTkAgg(self._fig, master=cv_f)
        NavigationToolbar2Tk(self._canvas, cv_f).update()
        self._canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        self._canvas.mpl_connect('button_press_event', self._on_canvas_click)
        self._canvas.mpl_connect('motion_notify_event', self._on_hover)

        self._status = tk.StringVar(value='Load a DEF/LEF to begin.')
        ttk.Label(root, textvariable=self._status, relief=tk.SUNKEN,
                  anchor='w', padding=(6,2)).pack(side=tk.BOTTOM, fill=tk.X)

    # ── File ops ──────────────────────────────────────────────────────────────

    def _browse_def(self):
        p = filedialog.askopenfilename(filetypes=[('DEF', '*.def'), ('All', '*')])
        if not p: return
        self._def_var.set(p)
        d = os.path.dirname(p); stem = re.sub(r'\.def$', '', p)
        for c in [stem+'.lef', stem.replace('.input','')+'.lef']:
            if os.path.exists(c): self._lef_var.set(c); return
        for f in sorted(os.listdir(d)):
            if f.endswith('.lef'): self._lef_var.set(os.path.join(d, f)); return

    def _browse_lef(self):
        p = filedialog.askopenfilename(filetypes=[('LEF', '*.lef'), ('All', '*')])
        if p: self._lef_var.set(p)

    def _load(self):
        def_p = self._def_var.get().strip(); lef_p = self._lef_var.get().strip()
        for lbl, p in [('DEF', def_p), ('LEF', lef_p)]:
            if not p or not os.path.exists(p):
                self._status.set(f'{lbl} not found.'); return
        self._status.set('Parsing…'); self.root.update_idletasks()
        try: msg = self.data.load(def_p, lef_p)
        except Exception as e: self._status.set(f'Error: {e}'); return
        self.selected_nets.clear(); self._sel_gid = None
        self._refresh_net_list(); self._inst_items = []; self._inst_lb.delete(0, tk.END)
        self._rebuild_grp_panel(); self._draw_canvas()
        self._status.set(f'Loaded: {msg}')

    def _save_groups(self):
        self.data.save_groups()
        self._status.set(f'Groups saved → {GroupStore.sidecar_path(self.data.def_path)}')

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
        self._refresh_inst_list(); self._draw_canvas()

    def _clear_selection(self):
        self.selected_nets.clear(); self._net_lb.selection_clear(0, tk.END)
        self._inst_items = []; self._inst_lb.delete(0, tk.END); self._draw_canvas()
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
        self._refresh_inst_list(); self._draw_canvas()
        if getattr(self, '_ipc', None) and sel:
            self._ipc.send({'type': 'select_inst', 'inst_names': sorted(sel)})

    # ── IPC ───────────────────────────────────────────────────────────────────

    def _on_ipc_message(self, msg: dict):
        kind = msg.get('type')
        if kind == 'select_bundle':
            all_nets = set(getattr(self.data, 'all_nets', []))
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
            self._refresh_inst_list(); self._draw_canvas()
        elif kind == 'clear':
            if self.selected_nets:
                self._clear_selection()

    # ── Groups panel ─────────────────────────────────────────────────────────

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
        """Create a new group from currently selected instances (inst panel selection)."""
        sel = {self._inst_items[i] for i in self._inst_lb.curselection() if i < len(self._inst_items)}
        if not sel:
            self._status.set('Select instances in the Instances panel first.'); return
        name = simpledialog.askstring('New Group', 'Group name:')
        if not name: return
        g = self.data.groups.new_group(name)
        for inst in sel: self.data.groups.add_inst(g.id, inst)
        self._sel_gid = g.id
        self._rebuild_grp_panel(); self._draw_canvas()
        self._status.set(f'Created group "{name}" with {len(sel)} instance(s).')

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
        sel = {self._inst_items[i] for i in self._inst_lb.curselection() if i < len(self._inst_items)}
        if not sel:
            self._status.set('Select instances in the Instances panel first.'); return
        for inst in sel: self.data.groups.add_inst(self._sel_gid, inst)
        self._rebuild_grp_panel(); self._draw_canvas()
        self._status.set(f'Added {len(sel)} instance(s) to "{self.data.groups.get(self._sel_gid).name}".')

    def _rm_insts_from_group(self):
        if not self._sel_gid: return
        sel = {self._inst_items[i] for i in self._inst_lb.curselection() if i < len(self._inst_items)}
        for inst in sel: self.data.groups.remove_inst(self._sel_gid, inst)
        insts = self.data.groups.all_insts(self._sel_gid)
        self._refresh_inst_list(override_list=insts)
        self._rebuild_grp_panel(); self._draw_canvas()

    # ── Canvas ────────────────────────────────────────────────────────────────

    def _draw_canvas(self):
        ax = self._ax; ax.clear()
        self._patch_inst.clear(); self._patch_group.clear(); self._hover_ann = None
        draw_die(ax, self.data.die)
        if self._show_all_bg.get(): draw_bg_instances(ax, self.data.inst_info)

        vis = self.data.visible_insts(self.selected_nets)
        self._patch_inst = draw_selected_instances(ax, vis, self.data, self.selected_nets)

        # Only draw visible groups
        visible_gids = {gid for gid, var in self._grp_vis.items() if var.get()}
        # Temporarily hide invisible groups by drawing only visible ones
        # We re-use draw_group_boxes but filter by visibility
        self._patch_group = _draw_group_boxes_filtered(
            ax, self.data.groups, self.data.inst_info,
            visible_gids, highlight_id=self._sel_gid)

        fit_view(ax, vis, self.data.inst_info, self.data.die, full_die=self._show_all_bg.get())
        ax.set_aspect('equal')
        ax.set_title(f'{len(vis)} inst(s) · {len(self.selected_nets)} net(s) · '
                     f'{len(self.data.groups.all_groups())} group(s)', fontsize=11)
        self._canvas.draw_idle()

    def _on_canvas_click(self, event):
        if event.inaxes != self._ax: return
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
                grps = [g.name for g in self.data.groups.groups_containing(inst)]
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
        for child_id in g.children:
            child = groups.get(child_id)
            if child: _draw(child, depth+1)

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
    if len(args) >= 1: app._def_var.set(os.path.abspath(args[0]))
    if len(args) >= 2: app._lef_var.set(os.path.abspath(args[1]))
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

    root.lift(); root.attributes('-topmost', True)
    root.after(500, lambda: root.attributes('-topmost', False))
    root.mainloop()

if __name__ == '__main__':
    main()
