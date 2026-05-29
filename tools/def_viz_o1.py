#!/usr/bin/env python3
"""
DEF Visualizer — V1: Treeview groups panel.

Groups panel shows the full hierarchy with expand/collapse.
Right-click for context menu (New child, Rename, Delete, Add/Remove insts).
Canvas shows colored bounding boxes for every group simultaneously.

Usage: python3 def_viz_v1.py [file.def [file.lef]]
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


class DefVizV1:
    def __init__(self, root):
        self.root  = root
        root.title('DEF Viz — V1: Treeview Groups')
        root.geometry('1600x860')

        self.data          = DefVizData()
        self.selected_nets = set()
        self._pending_ipc_msg = None
        self._net_items    = []
        self._inst_items   = []
        self._patch_inst   = {}
        self._patch_group  = {}
        self._hover_ann    = None
        self._updating     = False
        self._show_all_bg  = tk.BooleanVar(value=False)
        self._highlight_gid = None   # group highlighted on canvas

        self._build_ui()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = self.root

        # Top bar
        top = ttk.Frame(root, padding=(6, 4))
        top.pack(side=tk.TOP, fill=tk.X)
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

        main = ttk.Frame(root)
        main.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=4, pady=2)

        # ── Nets panel ────────────────────────────────────────────────────────
        np = ttk.LabelFrame(main, text='Nets', padding=4, width=190)
        np.pack(side=tk.LEFT, fill=tk.Y, padx=(0,4))
        np.pack_propagate(False)
        nf = ttk.Frame(np); nf.pack(fill=tk.X, pady=(0,2))
        ttk.Label(nf, text='Filter:').pack(side=tk.LEFT)
        self._net_filter = tk.StringVar()
        self._net_filter.trace_add('write', lambda *_: self._refresh_net_list())
        ttk.Entry(nf, textvariable=self._net_filter).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4,0))
        nlb_f = ttk.Frame(np); nlb_f.pack(fill=tk.BOTH, expand=True)
        self._net_lb = tk.Listbox(nlb_f, selectmode=tk.EXTENDED, width=22,
                                   exportselection=False, font=('Courier', 9))
        nsb = ttk.Scrollbar(nlb_f, orient=tk.VERTICAL, command=self._net_lb.yview)
        self._net_lb.configure(yscrollcommand=nsb.set)
        self._net_lb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        nsb.pack(side=tk.LEFT, fill=tk.Y)
        self._net_lb.bind('<<ListboxSelect>>', self._on_net_select)
        ttk.Button(np, text='Clear selection', command=self._clear_selection).pack(fill=tk.X, pady=(4,0))
        ttk.Checkbutton(np, text='Show all instances',
                        variable=self._show_all_bg, command=self._draw_canvas).pack(anchor='w', pady=(2,0))

        # ── Inst panel ────────────────────────────────────────────────────────
        ip = ttk.LabelFrame(main, text='Instances', padding=4, width=200)
        ip.pack(side=tk.LEFT, fill=tk.Y, padx=(0,4))
        ip.pack_propagate(False)
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
        self._inst_lb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        isb.pack(side=tk.LEFT, fill=tk.Y)
        self._inst_lb.bind('<<ListboxSelect>>', self._on_inst_select)

        # ── Groups Treeview panel ─────────────────────────────────────────────
        gp = ttk.LabelFrame(main, text='Groups', padding=4, width=230)
        gp.pack(side=tk.LEFT, fill=tk.Y, padx=(0,4))
        gp.pack_propagate(False)
        tv_f = ttk.Frame(gp); tv_f.pack(fill=tk.BOTH, expand=True)
        self._tv = ttk.Treeview(tv_f, columns=('count',), show='tree headings',
                                 selectmode='browse', height=30)
        self._tv.heading('#0',      text='Name')
        self._tv.heading('count',   text='#')
        self._tv.column('#0',       width=155, stretch=True)
        self._tv.column('count',    width=35,  stretch=False, anchor='e')
        tsb = ttk.Scrollbar(tv_f, orient=tk.VERTICAL, command=self._tv.yview)
        self._tv.configure(yscrollcommand=tsb.set)
        self._tv.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tsb.pack(side=tk.LEFT, fill=tk.Y)
        self._tv.bind('<<TreeviewSelect>>', self._on_tv_select)
        self._tv.bind('<Button-2>', self._on_tv_rightclick)   # Mac
        self._tv.bind('<Button-3>', self._on_tv_rightclick)   # Linux/Win
        self._tv.bind('<Double-1>', self._on_tv_double)

        btn_f = ttk.Frame(gp); btn_f.pack(fill=tk.X, pady=(4,0))
        ttk.Button(btn_f, text='New group',   command=self._new_root_group).pack(side=tk.LEFT, expand=True, fill=tk.X)
        ttk.Button(btn_f, text='Save',        command=self._save_groups).pack(side=tk.LEFT, expand=True, fill=tk.X)

        # ── Canvas ────────────────────────────────────────────────────────────
        cv_f = ttk.Frame(main)
        cv_f.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._fig = Figure(figsize=(9, 7), facecolor='#ececec')
        self._ax  = self._fig.add_subplot(111)
        self._canvas = FigureCanvasTkAgg(self._fig, master=cv_f)
        NavigationToolbar2Tk(self._canvas, cv_f).update()
        self._canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        self._canvas.mpl_connect('button_press_event', self._on_canvas_click)
        self._canvas.mpl_connect('motion_notify_event', self._on_hover)

        # Status
        self._status = tk.StringVar(value='Load a DEF/LEF to begin.')
        ttk.Label(root, textvariable=self._status, relief=tk.SUNKEN,
                  anchor='w', padding=(6,2)).pack(side=tk.BOTTOM, fill=tk.X)

    # ── File ops ──────────────────────────────────────────────────────────────

    def _browse_def(self):
        p = filedialog.askopenfilename(filetypes=[('DEF', '*.def'), ('All', '*')])
        if not p: return
        self._def_var.set(p)
        d = os.path.dirname(p)
        stem = re.sub(r'\.def$', '', p)
        for c in [stem + '.lef', stem.replace('.input','') + '.lef']:
            if os.path.exists(c): self._lef_var.set(c); return
        for f in sorted(os.listdir(d)):
            if f.endswith('.lef'): self._lef_var.set(os.path.join(d, f)); return

    def _browse_lef(self):
        p = filedialog.askopenfilename(filetypes=[('LEF', '*.lef'), ('All', '*')])
        if p: self._lef_var.set(p)

    def _load(self):
        def_p = self._def_var.get().strip()
        lef_p = self._lef_var.get().strip()
        for lbl, p in [('DEF', def_p), ('LEF', lef_p)]:
            if not p or not os.path.exists(p):
                self._status.set(f'{lbl} not found.'); return
        self._status.set('Parsing…'); self.root.update_idletasks()
        try:
            msg = self.data.load(def_p, lef_p)
        except Exception as e:
            self._status.set(f'Error: {e}'); return
        self.selected_nets.clear()
        self._refresh_net_list()
        self._inst_items = []; self._inst_lb.delete(0, tk.END)
        self._refresh_tv()
        self._draw_canvas()
        self._status.set(f'Loaded: {msg}')
        if self._pending_ipc_msg is not None:
            pending, self._pending_ipc_msg = self._pending_ipc_msg, None
            self._on_ipc_message(pending)

    def _save_groups(self):
        self.data.save_groups()
        self._status.set(f'Groups saved → {GroupStore.sidecar_path(self.data.def_path)}')

    # ── Net list ──────────────────────────────────────────────────────────────

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
        self._inst_items = []; self._inst_lb.delete(0, tk.END)
        self._draw_canvas()
        if getattr(self, '_ipc', None):
            self._ipc.send({'type': 'clear'})

    # ── Inst list ─────────────────────────────────────────────────────────────

    def _refresh_inst_list(self):
        filt = self._inst_filter.get().lower()
        vis  = self.data.visible_insts(self.selected_nets)
        self._inst_items = [i for i in vis if filt in i.lower()]
        self._inst_lb.delete(0, tk.END)
        for n in self._inst_items:
            cell = self.data.inst_info.get(n, {}).get('cell', '?')
            self._inst_lb.insert(tk.END, f'{n}  ({cell})')

    def _on_inst_select(self, _=None):
        if self._updating: return
        sel = {self._inst_items[i] for i in self._inst_lb.curselection()
               if i < len(self._inst_items)}
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
            self._refresh_inst_list(); self._draw_canvas()
        elif kind == 'clear':
            if self.selected_nets:
                self._clear_selection()

    # ── Groups Treeview ───────────────────────────────────────────────────────

    def _refresh_tv(self):
        self._tv.delete(*self._tv.get_children())
        def _insert(g, parent_tv_id=''):
            n   = len(self.data.groups.all_insts(g.id))
            iid = self._tv.insert(parent_tv_id, 'end', iid=g.id,
                                  text=f'  {g.name}', values=(n,),
                                  tags=(g.id,))
            self._tv.tag_configure(g.id, foreground=g.color)
            for child_id in g.children:
                child = self.data.groups.get(child_id)
                if child: _insert(child, iid)
        for g in self.data.groups.roots():
            _insert(g)

    def _selected_gid(self):
        sel = self._tv.selection()
        return sel[0] if sel else None

    def _on_tv_select(self, _=None):
        gid = self._selected_gid()
        self._highlight_gid = gid
        self._draw_canvas()

    def _on_tv_double(self, event):
        # Rename on double-click
        gid = self._selected_gid()
        if not gid: return
        g = self.data.groups.get(gid)
        name = simpledialog.askstring('Rename', 'New name:', initialvalue=g.name)
        if name:
            self.data.groups.rename_group(gid, name)
            self._refresh_tv()

    def _on_tv_rightclick(self, event):
        gid = self._tv.identify_row(event.y)
        if gid: self._tv.selection_set(gid)
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label='New root group',   command=self._new_root_group)
        if gid:
            menu.add_command(label='New child group',  command=lambda: self._new_child_group(gid))
            menu.add_command(label='Rename',           command=lambda: self._rename_group(gid))
            menu.add_separator()
            menu.add_command(label='Add selected instances',    command=lambda: self._add_insts(gid))
            menu.add_command(label='Remove selected instances', command=lambda: self._remove_insts(gid))
            menu.add_separator()
            menu.add_command(label='Delete group',     command=lambda: self._delete_group(gid))
        menu.tk_popup(event.x_root, event.y_root)

    def _new_root_group(self):
        name = simpledialog.askstring('New Group', 'Group name:')
        if not name: return
        self.data.groups.new_group(name)
        self._refresh_tv(); self._draw_canvas()

    def _new_child_group(self, parent_id):
        name = simpledialog.askstring('New Child Group', 'Group name:')
        if not name: return
        self.data.groups.new_group(name, parent_id=parent_id)
        self._refresh_tv(); self._tv.item(parent_id, open=True)
        self._draw_canvas()

    def _rename_group(self, gid):
        g = self.data.groups.get(gid)
        if not g: return
        name = simpledialog.askstring('Rename', 'New name:', initialvalue=g.name)
        if name:
            self.data.groups.rename_group(gid, name)
            self._refresh_tv()

    def _add_insts(self, gid):
        sel = {self._inst_items[i] for i in self._inst_lb.curselection()
               if i < len(self._inst_items)}
        if not sel:
            self._status.set('Select instances in the Instances panel first.'); return
        for inst in sel: self.data.groups.add_inst(gid, inst)
        self._refresh_tv(); self._draw_canvas()
        self._status.set(f'Added {len(sel)} instance(s) to group.')

    def _remove_insts(self, gid):
        sel = {self._inst_items[i] for i in self._inst_lb.curselection()
               if i < len(self._inst_items)}
        for inst in sel: self.data.groups.remove_inst(gid, inst)
        self._refresh_tv(); self._draw_canvas()

    def _delete_group(self, gid):
        self.data.groups.delete_group(gid)
        self._highlight_gid = None
        self._refresh_tv(); self._draw_canvas()

    # ── Canvas ────────────────────────────────────────────────────────────────

    def _draw_canvas(self):
        ax = self._ax; ax.clear()
        self._patch_inst.clear(); self._patch_group.clear()
        self._hover_ann = None

        draw_die(ax, self.data.die)
        if self._show_all_bg.get(): draw_bg_instances(ax, self.data.inst_info)

        vis = self.data.visible_insts(self.selected_nets)
        self._patch_inst = draw_selected_instances(ax, vis, self.data, self.selected_nets)
        self._patch_group = draw_group_boxes(ax, self.data.groups, self.data.inst_info,
                                             highlight_id=self._highlight_gid)
        fit_view(ax, vis, self.data.inst_info, self.data.die,
                 full_die=self._show_all_bg.get())
        ax.set_aspect('equal')
        ax.set_title(f'{len(vis)} inst(s) · {len(self.selected_nets)} net(s) · '
                     f'{len(self.data.groups.all_groups())} group(s)', fontsize=11)
        self._canvas.draw_idle()

    def _on_canvas_click(self, event):
        if event.inaxes != self._ax: return
        tb = getattr(self._canvas, 'toolbar', None)
        if tb and getattr(tb, 'mode', '') != '': return
        # Check group patches first (on top)
        for patch, gid in list(self._patch_group.items()):
            if patch.contains(event)[0]:
                self._tv.selection_set(gid)
                self._tv.see(gid)
                self._highlight_gid = gid
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
                info  = self.data.inst_info.get(inst, {})
                nets  = sorted(self.data.inst_nets.get(inst, set()))
                grps  = [g.name for g in self.data.groups.groups_containing(inst)]
                tip   = f'{inst}\n{info.get("cell","")}'
                if nets: tip += f'\nnets: {", ".join(nets[:3])}{"…" if len(nets)>3 else ""}'
                if grps: tip += f'\ngroups: {", ".join(grps)}'
                self._set_hover(event, tip); return
        self._clear_hover()

    def _set_hover(self, event, text):
        self._clear_hover()
        self._hover_ann = self._ax.annotate(
            text, xy=(event.xdata, event.ydata),
            xytext=(12, 12), textcoords='offset points', fontsize=7.5,
            bbox=dict(boxstyle='round,pad=0.4', fc='lightyellow', ec='#aaaaaa', alpha=0.95),
            zorder=30)
        self._canvas.draw_idle()

    def _clear_hover(self):
        if self._hover_ann:
            try: self._hover_ann.remove()
            except Exception: pass
            self._hover_ann = None
            self._canvas.draw_idle()


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
    app = DefVizV1(root)
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

    root.lift(); root.focus_force()
    root.mainloop()

if __name__ == '__main__':
    main()
