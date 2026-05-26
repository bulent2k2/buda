#!/usr/bin/env python3
"""
DEF Visualizer — V2: Breadcrumb groups navigator.

Groups panel shows one level at a time.  Double-click a group to drill in;
breadcrumb at the top shows the path; Back button goes up one level.
Clicking a group selects it on the canvas; its sub-groups and instances are
shown in the Contents list below.

Usage: python3 def_viz_v2.py [file.def [file.lef]]
"""

import os, sys, re, tkinter as tk
from tkinter import ttk, filedialog, simpledialog

import matplotlib
matplotlib.use('TkAgg')
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
from def_viz_shared import (DefVizData, GroupStore,
                             draw_die, draw_bg_instances,
                             draw_selected_instances, draw_group_boxes, fit_view)


class DefVizV2:
    def __init__(self, root):
        self.root  = root
        root.title('DEF Viz — V2: Breadcrumb Groups')
        root.geometry('1620x860')

        self.data          = DefVizData()
        self.selected_nets = set()
        self._net_items    = []
        self._inst_items   = []
        self._patch_inst   = {}
        self._patch_group  = {}
        self._hover_ann    = None
        self._updating     = False
        self._show_all_bg  = tk.BooleanVar(value=False)

        # Breadcrumb nav state: list of group ids; empty = root level
        self._nav_path: list[str] = []
        # Groups shown in the current nav level
        self._level_groups: list[str] = []   # group ids at current level
        # Selected group id (highlighted on canvas)
        self._sel_gid: str = None

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

        # ── Nets panel ────────────────────────────────────────────────────────
        np = ttk.LabelFrame(main, text='Nets', padding=4, width=190)
        np.pack(side=tk.LEFT, fill=tk.Y, padx=(0,4)); np.pack_propagate(False)
        nf = ttk.Frame(np); nf.pack(fill=tk.X, pady=(0,2))
        ttk.Label(nf, text='Filter:').pack(side=tk.LEFT)
        self._net_filter = tk.StringVar()
        self._net_filter.trace_add('write', lambda *_: self._refresh_net_list())
        ttk.Entry(nf, textvariable=self._net_filter).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4,0))
        nlb_f = ttk.Frame(np); nlb_f.pack(fill=tk.BOTH, expand=True)
        self._net_lb = tk.Listbox(nlb_f, selectmode=tk.EXTENDED, width=22,
                                   exportselection=False, font=('Courier', 9))
        nsb = ttk.Scrollbar(nlb_f, orient=tk.VERTICAL, command=self._net_lb.yview)
        self._net_lb.configure(yscrollcommand=nsb.set); self._net_lb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True); nsb.pack(side=tk.LEFT, fill=tk.Y)
        self._net_lb.bind('<<ListboxSelect>>', self._on_net_select)
        ttk.Button(np, text='Clear selection', command=self._clear_selection).pack(fill=tk.X, pady=(4,0))
        ttk.Checkbutton(np, text='Show all instances',
                        variable=self._show_all_bg, command=self._draw_canvas).pack(anchor='w', pady=(2,0))

        # ── Inst panel ────────────────────────────────────────────────────────
        ip = ttk.LabelFrame(main, text='Instances', padding=4, width=200)
        ip.pack(side=tk.LEFT, fill=tk.Y, padx=(0,4)); ip.pack_propagate(False)
        iff = ttk.Frame(ip); iff.pack(fill=tk.X, pady=(0,2))
        ttk.Label(iff, text='Filter:').pack(side=tk.LEFT)
        self._inst_filter = tk.StringVar()
        self._inst_filter.trace_add('write', lambda *_: self._refresh_inst_list())
        ttk.Entry(iff, textvariable=self._inst_filter).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4,0))
        ilb_f = ttk.Frame(ip); ilb_f.pack(fill=tk.BOTH, expand=True)
        self._inst_lb = tk.Listbox(ilb_f, selectmode=tk.EXTENDED, width=24,
                                    exportselection=False, font=('Courier', 9))
        isb = ttk.Scrollbar(ilb_f, orient=tk.VERTICAL, command=self._inst_lb.yview)
        self._inst_lb.configure(yscrollcommand=isb.set); self._inst_lb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True); isb.pack(side=tk.LEFT, fill=tk.Y)
        self._inst_lb.bind('<<ListboxSelect>>', self._on_inst_select)

        # ── Groups navigator panel ────────────────────────────────────────────
        gp = ttk.LabelFrame(main, text='Groups', padding=4, width=250)
        gp.pack(side=tk.LEFT, fill=tk.Y, padx=(0,4)); gp.pack_propagate(False)

        # Breadcrumb bar
        bc_f = ttk.Frame(gp); bc_f.pack(fill=tk.X, pady=(0,3))
        self._btn_back = ttk.Button(bc_f, text='↑ Up', width=5, command=self._nav_up)
        self._btn_back.pack(side=tk.LEFT, padx=(0,4))
        self._crumb_var = tk.StringVar(value='/ (root)')
        ttk.Label(bc_f, textvariable=self._crumb_var, font=('TkDefaultFont', 9, 'italic'),
                  foreground='#555555').pack(side=tk.LEFT)

        # Group list at current level
        glb_f = ttk.Frame(gp); glb_f.pack(fill=tk.BOTH, expand=True)
        self._grp_lb = tk.Listbox(glb_f, selectmode=tk.BROWSE, width=28,
                                   exportselection=False, font=('Courier', 9))
        gsb = ttk.Scrollbar(glb_f, orient=tk.VERTICAL, command=self._grp_lb.yview)
        self._grp_lb.configure(yscrollcommand=gsb.set)
        self._grp_lb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        gsb.pack(side=tk.LEFT, fill=tk.Y)
        self._grp_lb.bind('<<ListboxSelect>>', self._on_grp_select)
        self._grp_lb.bind('<Double-1>', self._on_grp_double)

        # Separator + Contents list label
        ttk.Separator(gp, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=3)
        ttk.Label(gp, text='Contents of selected group:',
                  font=('TkDefaultFont', 8)).pack(anchor='w')
        ct_f = ttk.Frame(gp); ct_f.pack(fill=tk.BOTH, expand=True)
        self._cont_lb = tk.Listbox(ct_f, selectmode=tk.EXTENDED, width=28,
                                    exportselection=False, font=('Courier', 8))
        csb = ttk.Scrollbar(ct_f, orient=tk.VERTICAL, command=self._cont_lb.yview)
        self._cont_lb.configure(yscrollcommand=csb.set)
        self._cont_lb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        csb.pack(side=tk.LEFT, fill=tk.Y)

        # Buttons
        bf = ttk.Frame(gp); bf.pack(fill=tk.X, pady=(4,0))
        ttk.Button(bf, text='New',      command=self._new_group).pack(side=tk.LEFT, expand=True, fill=tk.X)
        ttk.Button(bf, text='Delete',   command=self._delete_group).pack(side=tk.LEFT, expand=True, fill=tk.X)
        ttk.Button(bf, text='Add insts', command=self._add_insts).pack(side=tk.LEFT, expand=True, fill=tk.X)
        ttk.Button(bf, text='Rm insts',  command=self._rm_insts).pack(side=tk.LEFT, expand=True, fill=tk.X)
        ttk.Button(bf, text='Save',      command=self._save_groups).pack(fill=tk.X, pady=(2,0))

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
        self.selected_nets.clear(); self._nav_path = []; self._sel_gid = None
        self._refresh_net_list(); self._inst_items = []; self._inst_lb.delete(0, tk.END)
        self._refresh_grp_nav(); self._draw_canvas()
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

    def _refresh_inst_list(self):
        filt = self._inst_filter.get().lower()
        vis  = self.data.visible_insts(self.selected_nets)
        self._inst_items = [i for i in vis if filt in i.lower()]
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

    # ── Groups navigator ──────────────────────────────────────────────────────

    def _current_parent_id(self):
        return self._nav_path[-1] if self._nav_path else None

    def _refresh_grp_nav(self):
        """Rebuild the group listbox for the current nav level."""
        parent_id = self._current_parent_id()
        if parent_id:
            parent = self.data.groups.get(parent_id)
            self._level_groups = list(parent.children) if parent else []
            crumb = ' > '.join(
                self.data.groups.get(gid).name
                for gid in self._nav_path
                if self.data.groups.get(gid))
            self._crumb_var.set(f'/ {crumb}')
        else:
            self._level_groups = [g.id for g in self.data.groups.roots()]
            self._crumb_var.set('/ (root)')

        self._grp_lb.delete(0, tk.END)
        for gid in self._level_groups:
            g = self.data.groups.get(gid)
            if not g: continue
            n      = len(self.data.groups.all_insts(gid))
            has_ch = len(g.children) > 0
            prefix = '▶ ' if has_ch else '  '
            self._grp_lb.insert(tk.END, f'{prefix}{g.name}  ({n})')
            # Color the entry with group color using tag hack via fg on individual items
            idx = self._grp_lb.size() - 1
            self._grp_lb.itemconfig(idx, fg=g.color)

        self._refresh_contents()
        self._btn_back.state(['disabled'] if not self._nav_path else ['!disabled'])

    def _refresh_contents(self):
        """Show direct children (sub-groups + instances) of the selected group."""
        self._cont_lb.delete(0, tk.END)
        if not self._sel_gid:
            return
        g = self.data.groups.get(self._sel_gid)
        if not g:
            return
        for child_id in g.children:
            child = self.data.groups.get(child_id)
            if child:
                self._cont_lb.insert(tk.END, f'▶ {child.name}')
                self._cont_lb.itemconfig(self._cont_lb.size()-1, fg=child.color)
        for inst in g.instances:
            cell = self.data.inst_info.get(inst, {}).get('cell', '?')
            self._cont_lb.insert(tk.END, f'  {inst}  ({cell})')

    def _on_grp_select(self, _=None):
        sel = self._grp_lb.curselection()
        if not sel or sel[0] >= len(self._level_groups): return
        self._sel_gid = self._level_groups[sel[0]]
        self._refresh_contents()
        self._draw_canvas()

    def _on_grp_double(self, event):
        """Double-click a group → navigate into it."""
        sel = self._grp_lb.curselection()
        if not sel or sel[0] >= len(self._level_groups): return
        gid = self._level_groups[sel[0]]
        g   = self.data.groups.get(gid)
        if g and g.children:
            self._nav_path.append(gid)
            self._sel_gid = None
            self._refresh_grp_nav()
            self._draw_canvas()

    def _nav_up(self):
        if self._nav_path:
            self._nav_path.pop()
            self._sel_gid = None
            self._refresh_grp_nav()
            self._draw_canvas()

    def _new_group(self):
        name = simpledialog.askstring('New Group', 'Group name:')
        if not name: return
        parent_id = self._sel_gid or self._current_parent_id()
        self.data.groups.new_group(name, parent_id=parent_id)
        self._refresh_grp_nav(); self._draw_canvas()

    def _delete_group(self):
        if not self._sel_gid: return
        self.data.groups.delete_group(self._sel_gid)
        self._sel_gid = None
        self._refresh_grp_nav(); self._draw_canvas()

    def _add_insts(self):
        if not self._sel_gid:
            self._status.set('Select a group first.'); return
        sel = {self._inst_items[i] for i in self._inst_lb.curselection() if i < len(self._inst_items)}
        if not sel:
            self._status.set('Select instances in the Instances panel first.'); return
        for inst in sel: self.data.groups.add_inst(self._sel_gid, inst)
        self._refresh_contents(); self._draw_canvas()
        self._status.set(f'Added {len(sel)} instance(s).')

    def _rm_insts(self):
        if not self._sel_gid: return
        # Remove instances selected in the Contents list
        g = self.data.groups.get(self._sel_gid)
        if not g: return
        sel_idx = self._cont_lb.curselection()
        to_remove = []
        inst_rows = [(i, self._cont_lb.get(i)) for i in sel_idx]
        for _, text in inst_rows:
            # Instance rows start with '  ', group rows with '▶ '
            if text.startswith('  '):
                inst = text.strip().split('  ')[0]
                to_remove.append(inst)
        for inst in to_remove: self.data.groups.remove_inst(self._sel_gid, inst)
        self._refresh_contents(); self._draw_canvas()

    # ── Canvas ────────────────────────────────────────────────────────────────

    def _draw_canvas(self):
        ax = self._ax; ax.clear()
        self._patch_inst.clear(); self._patch_group.clear(); self._hover_ann = None
        draw_die(ax, self.data.die)
        if self._show_all_bg.get(): draw_bg_instances(ax, self.data.inst_info)
        vis = self.data.visible_insts(self.selected_nets)
        self._patch_inst  = draw_selected_instances(ax, vis, self.data, self.selected_nets)
        self._patch_group = draw_group_boxes(ax, self.data.groups, self.data.inst_info,
                                             highlight_id=self._sel_gid)
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
                # Navigate to / select the group
                self._sel_gid = gid
                g = self.data.groups.get(gid)
                # Rebuild nav path to point to the group's parent level
                if g:
                    path = []
                    cur = g.parent
                    while cur:
                        path.insert(0, cur)
                        pg = self.data.groups.get(cur)
                        cur = pg.parent if pg else None
                    self._nav_path = path
                self._refresh_grp_nav()
                # Select the row in the listbox
                if gid in self._level_groups:
                    idx = self._level_groups.index(gid)
                    self._grp_lb.selection_clear(0, tk.END)
                    self._grp_lb.selection_set(idx); self._grp_lb.see(idx)
                self._draw_canvas(); return
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
    app = DefVizV2(root)
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
