#!/usr/bin/env python3
"""
DEF/LEF visualizer.

Usage:
    python3 def_viz.py [file.def [file.lef]]

Select nets in the left panel → connected instances appear on the canvas.
Click an instance on the canvas (or in the middle panel) → the instance's
nets are added to the net selection, expanding the visible set.
"""

import os
import re
import sys
import tkinter as tk
from tkinter import ttk, filedialog

import matplotlib
matplotlib.use('TkAgg')
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure
import matplotlib.patches as mpatches

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
from def_cluster import parse_def, parse_lef

# ── Palette ───────────────────────────────────────────────────────────────────
_C_DRIVER   = '#FF8C66'   # drives a selected net
_C_RECEIVER = '#66AAFF'   # receives a selected net
_C_BOTH     = '#CC88FF'   # driver on one net, receiver on another
_C_EDGE     = '#444444'
_C_DIE_FILL = '#f6f6f6'
_C_DIE_EDGE = '#999999'


def _parse_cell_sizes(lef_path):
    """Return {cell_name: (width, height)} from MACRO SIZE entries in LEF."""
    with open(lef_path) as f:
        content = f.read()
    sizes = {}
    for m in re.finditer(
            r'MACRO\s+(\S+).*?SIZE\s+([\d.]+)\s+BY\s+([\d.]+)\s*;',
            content, re.DOTALL):
        sizes[m.group(1)] = (float(m.group(2)), float(m.group(3)))
    return sizes


class DefViz:
    def __init__(self, root):
        self.root = root
        root.title('DEF Visualizer')
        root.geometry('1440x820')

        # ── Parsed data ───────────────────────────────────────────────────────
        self.units      = 1000
        self.die        = (0.0, 0.0)
        self.inst_info  = {}   # inst -> {x1,y1,x2,y2,cell}
        self.net_insts  = {}   # net_name -> set(inst_names)
        self.inst_nets  = {}   # inst_name -> set(net_names)
        self.inst_roles = {}   # (net_name, inst_name) -> 'driver'|'receiver'
        self._all_nets  = []   # sorted list of all net names
        self._pending_ipc_msg = None   # IPC selection that arrived before DEF loaded

        # ── Selection state ───────────────────────────────────────────────────
        self.selected_nets  = set()
        self._net_items     = []   # names in net listbox (after filter)
        self._inst_items    = []   # names in inst listbox (after filter)
        self._patch_inst    = {}   # patch -> inst_name
        self._hover_ann     = None
        self._updating      = False   # re-entrance guard
        self._show_all_bg   = tk.BooleanVar(value=False)

        self._build_ui()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = self.root

        # ── Top bar: file paths ───────────────────────────────────────────────
        top = ttk.Frame(root, padding=(6, 4))
        top.pack(side=tk.TOP, fill=tk.X)

        ttk.Label(top, text='DEF:').grid(row=0, column=0, sticky='w', padx=(0, 4))
        self._def_var = tk.StringVar()
        ttk.Entry(top, textvariable=self._def_var, width=70).grid(row=0, column=1, sticky='ew', padx=2)
        ttk.Button(top, text='Browse…', command=self._browse_def).grid(row=0, column=2, padx=4)

        ttk.Label(top, text='LEF:').grid(row=1, column=0, sticky='w', padx=(0, 4))
        self._lef_var = tk.StringVar()
        ttk.Entry(top, textvariable=self._lef_var, width=70).grid(row=1, column=1, sticky='ew', padx=2)
        ttk.Button(top, text='Browse…', command=self._browse_lef).grid(row=1, column=2, padx=4)

        ttk.Button(top, text='Load', command=self._load,
                   width=10).grid(row=0, column=3, rowspan=2, padx=10, sticky='ns')
        top.columnconfigure(1, weight=1)

        # ── Main area ─────────────────────────────────────────────────────────
        main = ttk.Frame(root)
        main.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=4, pady=2)

        # Net panel
        net_panel = ttk.LabelFrame(main, text='Nets', padding=4)
        net_panel.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 4))
        net_panel.configure(width=195)
        net_panel.pack_propagate(False)

        nf = ttk.Frame(net_panel)
        nf.pack(fill=tk.X, pady=(0, 2))
        ttk.Label(nf, text='Filter:').pack(side=tk.LEFT)
        self._net_filter = tk.StringVar()
        self._net_filter.trace_add('write', lambda *_: self._refresh_net_list())
        ttk.Entry(nf, textvariable=self._net_filter).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))

        nlb_frame = ttk.Frame(net_panel)
        nlb_frame.pack(fill=tk.BOTH, expand=True)
        self._net_lb = tk.Listbox(nlb_frame, selectmode=tk.EXTENDED, width=22,
                                   exportselection=False, font=('Courier', 9))
        nsb = ttk.Scrollbar(nlb_frame, orient=tk.VERTICAL, command=self._net_lb.yview)
        self._net_lb.configure(yscrollcommand=nsb.set)
        self._net_lb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        nsb.pack(side=tk.LEFT, fill=tk.Y)
        self._net_lb.bind('<<ListboxSelect>>', self._on_net_select)

        ttk.Button(net_panel, text='Clear selection',
                   command=self._clear_selection).pack(fill=tk.X, pady=(4, 0))
        ttk.Checkbutton(net_panel, text='Show all instances',
                        variable=self._show_all_bg,
                        command=self._draw_canvas).pack(anchor='w', pady=(2, 0))

        # Instance panel
        inst_panel = ttk.LabelFrame(main, text='Instances', padding=4)
        inst_panel.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 4))
        inst_panel.configure(width=215)
        inst_panel.pack_propagate(False)

        if_ = ttk.Frame(inst_panel)
        if_.pack(fill=tk.X, pady=(0, 2))
        ttk.Label(if_, text='Filter:').pack(side=tk.LEFT)
        self._inst_filter = tk.StringVar()
        self._inst_filter.trace_add('write', lambda *_: self._refresh_inst_list())
        ttk.Entry(if_, textvariable=self._inst_filter).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))

        ilb_frame = ttk.Frame(inst_panel)
        ilb_frame.pack(fill=tk.BOTH, expand=True)
        self._inst_lb = tk.Listbox(ilb_frame, selectmode=tk.EXTENDED, width=26,
                                    exportselection=False, font=('Courier', 9))
        isb = ttk.Scrollbar(ilb_frame, orient=tk.VERTICAL, command=self._inst_lb.yview)
        self._inst_lb.configure(yscrollcommand=isb.set)
        self._inst_lb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        isb.pack(side=tk.LEFT, fill=tk.Y)
        self._inst_lb.bind('<<ListboxSelect>>', self._on_inst_select)

        # Canvas
        cv_frame = ttk.Frame(main)
        cv_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._fig = Figure(figsize=(9, 7), facecolor='#ececec')
        self._ax  = self._fig.add_subplot(111)
        self._canvas = FigureCanvasTkAgg(self._fig, master=cv_frame)
        toolbar = NavigationToolbar2Tk(self._canvas, cv_frame)
        toolbar.update()
        self._canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        self._canvas.mpl_connect('button_press_event', self._on_canvas_click)
        self._canvas.mpl_connect('motion_notify_event', self._on_hover)

        # Status bar
        self._status = tk.StringVar(value='Load a DEF/LEF file to begin.')
        ttk.Label(root, textvariable=self._status, relief=tk.SUNKEN,
                  anchor='w', padding=(6, 2)).pack(side=tk.BOTTOM, fill=tk.X)

    # ── File browsing ─────────────────────────────────────────────────────────

    def _browse_def(self):
        p = filedialog.askopenfilename(
            title='Select DEF file',
            filetypes=[('DEF files', '*.def *.input.def'), ('All files', '*')])
        if not p:
            return
        self._def_var.set(p)
        # Auto-fill LEF: same stem, or first .lef in same directory
        d = os.path.dirname(p)
        stem = re.sub(r'\.def$', '', p)
        for candidate in [stem + '.lef', stem.replace('.input', '') + '.lef']:
            if os.path.exists(candidate):
                self._lef_var.set(candidate)
                return
        for f in sorted(os.listdir(d)):
            if f.endswith('.lef'):
                self._lef_var.set(os.path.join(d, f))
                return

    def _browse_lef(self):
        p = filedialog.askopenfilename(
            title='Select LEF file',
            filetypes=[('LEF files', '*.lef *.input.lef'), ('All files', '*')])
        if p:
            self._lef_var.set(p)

    # ── Load ──────────────────────────────────────────────────────────────────

    def _load(self):
        def_path = self._def_var.get().strip()
        lef_path = self._lef_var.get().strip()
        for label, path in [('DEF', def_path), ('LEF', lef_path)]:
            if not path:
                self._status.set(f'{label} path is empty.')
                return
            if not os.path.exists(path):
                self._status.set(f'{label} file not found: {path}')
                return

        self._status.set('Parsing DEF/LEF…')
        self.root.update_idletasks()

        try:
            units, die, components, nets_raw = parse_def(def_path)
            lef_pins = parse_lef(lef_path)
            cell_sizes = _parse_cell_sizes(lef_path)
        except Exception as exc:
            self._status.set(f'Parse error: {exc}')
            return

        self.units = units
        self.die   = die

        self.inst_info  = {}
        self.net_insts  = {}
        self.inst_nets  = {}
        self.inst_roles = {}

        for net_name, conns in nets_raw.items():
            inst_set = set()
            for inst, pin in conns:
                if inst not in components:
                    continue
                cell, dx, dy, _ = components[inst]
                x1 = dx / units
                y1 = dy / units
                w, h = cell_sizes.get(cell, (0.5, 0.5))
                self.inst_info[inst] = {
                    'x1': x1, 'y1': y1, 'x2': x1 + w, 'y2': y1 + h, 'cell': cell
                }
                inst_set.add(inst)
                pin_dir = 'UNKNOWN'
                if cell in lef_pins and pin in lef_pins[cell]:
                    pin_dir = lef_pins[cell][pin][2]
                role = 'driver' if pin_dir == 'OUTPUT' else 'receiver'
                key = (net_name, inst)
                if key not in self.inst_roles or role == 'driver':
                    self.inst_roles[key] = role
                self.inst_nets.setdefault(inst, set()).add(net_name)
            self.net_insts[net_name] = inst_set

        self._all_nets    = sorted(nets_raw.keys())
        self.selected_nets.clear()

        self._refresh_net_list()
        self._inst_items = []
        self._inst_lb.delete(0, tk.END)
        self._draw_canvas()
        self._status.set(
            f'Loaded: {len(self._all_nets)} nets · {len(self.inst_info)} instances · '
            f'die {die[0]:.1f}×{die[1]:.1f} µm')

        if self._pending_ipc_msg is not None:
            msg, self._pending_ipc_msg = self._pending_ipc_msg, None
            self._on_ipc_message(msg)

    # ── Net listbox ───────────────────────────────────────────────────────────

    def _refresh_net_list(self):
        filt = self._net_filter.get().lower()
        self._net_items = [n for n in self._all_nets if filt in n.lower()]
        self._net_lb.delete(0, tk.END)
        for name in self._net_items:
            self._net_lb.insert(tk.END, name)
        # Re-apply current selection highlights
        self._updating = True
        try:
            for i, name in enumerate(self._net_items):
                if name in self.selected_nets:
                    self._net_lb.selection_set(i)
        finally:
            self._updating = False

    def _on_net_select(self, _event=None):
        if self._updating:
            return
        sel = self._net_lb.curselection()
        self.selected_nets = {self._net_items[i] for i in sel
                              if 0 <= i < len(self._net_items)}
        self._refresh_inst_list()
        self._draw_canvas()
        vis = self._visible_insts()
        self._status.set(
            f'{len(self.selected_nets)} net(s) selected → {len(vis)} instance(s) visible')

    def _clear_selection(self):
        self.selected_nets.clear()
        self._net_lb.selection_clear(0, tk.END)
        self._inst_items = []
        self._inst_lb.delete(0, tk.END)
        self._draw_canvas()
        self._status.set('Selection cleared.')
        if getattr(self, '_ipc', None):
            self._ipc.send({'type': 'clear'})

    # ── Instance listbox ──────────────────────────────────────────────────────

    def _visible_insts(self):
        """Sorted inst names connected to any selected net."""
        result = set()
        for net in self.selected_nets:
            result |= self.net_insts.get(net, set())
        return sorted(result)

    def _refresh_inst_list(self):
        filt = self._inst_filter.get().lower()
        visible = self._visible_insts()
        self._inst_items = [i for i in visible if filt in i.lower()]
        self._inst_lb.delete(0, tk.END)
        for name in self._inst_items:
            cell = self.inst_info.get(name, {}).get('cell', '?')
            self._inst_lb.insert(tk.END, f'{name}  ({cell})')

    def _on_inst_select(self, _event=None):
        if self._updating:
            return
        sel = self._inst_lb.curselection()
        sel_insts = {self._inst_items[i] for i in sel
                     if 0 <= i < len(self._inst_items)}
        if not sel_insts:
            return

        # Expand net selection with all nets that touch any selected instance
        new_nets = set()
        for inst in sel_insts:
            new_nets |= self.inst_nets.get(inst, set())
        added = new_nets - self.selected_nets
        if not added:
            return

        self.selected_nets |= added

        # Sync net listbox selection without re-triggering this callback
        self._updating = True
        try:
            for i, name in enumerate(self._net_items):
                if name in self.selected_nets:
                    self._net_lb.selection_set(i)
        finally:
            self._updating = False

        self._refresh_inst_list()
        self._draw_canvas()
        vis = self._visible_insts()
        self._status.set(
            f'{len(self.selected_nets)} net(s) selected → {len(vis)} instance(s) visible')
        if getattr(self, '_ipc', None) and sel_insts:
            self._ipc.send({'type': 'select_inst', 'inst_names': sorted(sel_insts)})

    # ── IPC ───────────────────────────────────────────────────────────────────

    def _on_ipc_message(self, msg: dict):
        kind = msg.get('type')
        if kind == 'select_bundle':
            if not self._all_nets:
                self._pending_ipc_msg = msg
                return
            net_names = [n for n in msg.get('net_names', []) if n in set(self._all_nets)]
            if not net_names:
                return
            self.selected_nets = set(net_names)
            self._updating = True
            try:
                self._net_lb.selection_clear(0, tk.END)
                for i, name in enumerate(self._net_items):
                    if name in self.selected_nets:
                        self._net_lb.selection_set(i)
            finally:
                self._updating = False
            self._refresh_inst_list()
            self._draw_canvas()
            vis = self._visible_insts()
            bid = msg.get('bundle_id', '?')
            self._status.set(
                f'[IPC] bundle {bid}: {len(self.selected_nets)} net(s) → {len(vis)} instance(s)')
        elif kind == 'clear':
            if self.selected_nets:
                self.selected_nets.clear()
                self._net_lb.selection_clear(0, tk.END)
                self._inst_items = []
                self._inst_lb.delete(0, tk.END)
                self._draw_canvas()
                self._status.set('Selection cleared.')

    # ── Canvas ────────────────────────────────────────────────────────────────

    def _inst_color(self, inst):
        roles = {self.inst_roles.get((n, inst))
                 for n in self.selected_nets
                 if (n, inst) in self.inst_roles}
        if 'driver' in roles and 'receiver' in roles:
            return _C_BOTH
        if 'driver' in roles:
            return _C_DRIVER
        return _C_RECEIVER

    def _draw_canvas(self):
        ax = self._ax
        ax.clear()
        self._patch_inst.clear()
        self._hover_ann = None

        dw, dh = self.die
        if dw > 0 and dh > 0:
            ax.add_patch(mpatches.Rectangle(
                (0, 0), dw, dh,
                linewidth=1.2, edgecolor=_C_DIE_EDGE, facecolor=_C_DIE_FILL, zorder=0))

        # Background: all instances faintly, when toggled on
        if self._show_all_bg.get() and self.inst_info:
            for info in self.inst_info.values():
                ax.add_patch(mpatches.Rectangle(
                    (info['x1'], info['y1']),
                    info['x2'] - info['x1'], info['y2'] - info['y1'],
                    linewidth=0, facecolor='#bbbbbb', alpha=0.18, zorder=1))

        visible = self._visible_insts()
        if not visible:
            if dw > 0:
                margin = max(dw, dh) * 0.05
                ax.set_xlim(-margin, dw + margin)
                ax.set_ylim(-margin, dh + margin)
            ax.set_aspect('equal')
            ax.set_title('Select one or more nets to show connected instances', fontsize=12)
            self._canvas.draw_idle()
            return

        xs1 = xs2 = ys1 = ys2 = None
        for inst in visible:
            info = self.inst_info.get(inst)
            if info is None:
                continue
            x1, y1, x2, y2 = info['x1'], info['y1'], info['x2'], info['y2']
            color = self._inst_color(inst)
            patch = mpatches.Rectangle(
                (x1, y1), x2 - x1, y2 - y1,
                linewidth=0.8, edgecolor=_C_EDGE, facecolor=color,
                alpha=0.85, zorder=2, picker=True)
            ax.add_patch(patch)
            self._patch_inst[patch] = inst
            xs1 = min(xs1, x1) if xs1 is not None else x1
            ys1 = min(ys1, y1) if ys1 is not None else y1
            xs2 = max(xs2, x2) if xs2 is not None else x2
            ys2 = max(ys2, y2) if ys2 is not None else y2

        # Fit view: full die when background is shown, else tight around visible insts
        if self._show_all_bg.get() and dw > 0:
            margin = max(dw, dh) * 0.03
            ax.set_xlim(-margin, dw + margin)
            ax.set_ylim(-margin, dh + margin)
        else:
            pw = max(xs2 - xs1, 1.0) * 0.12
            ph = max(ys2 - ys1, 1.0) * 0.12
            ax.set_xlim(xs1 - pw, xs2 + pw)
            ax.set_ylim(ys1 - ph, ys2 + ph)

        # Labels: only when cells are large enough on screen
        # Estimate pixel size of the smallest cell
        fig_w_pt = self._fig.get_size_inches()[0] * self._fig.get_dpi()
        xlo, xhi = ax.get_xlim()
        data_w   = xhi - xlo
        pt_per_um = fig_w_pt / data_w if data_w > 0 else 0
        min_cell_w = min(
            (info['x2'] - info['x1']) for info in
            (self.inst_info[i] for i in visible if i in self.inst_info)
        )
        show_labels = (min_cell_w * pt_per_um) > 18

        if show_labels:
            for inst in visible:
                info = self.inst_info.get(inst)
                if info is None:
                    continue
                cx = (info['x1'] + info['x2']) / 2
                cy = (info['y1'] + info['y2']) / 2
                ax.text(cx, cy, inst, ha='center', va='center',
                        fontsize=6, color='#222222', clip_on=True, zorder=3)

        ax.set_aspect('equal')
        ax.set_xlabel('X (µm)', fontsize=9)
        ax.set_ylabel('Y (µm)', fontsize=9)
        ax.set_title(
            f'{len(visible)} instance(s)  ·  {len(self.selected_nets)} net(s) selected',
            fontsize=11)

        handles = [
            mpatches.Patch(facecolor=_C_DRIVER,   label='driver',             edgecolor=_C_EDGE),
            mpatches.Patch(facecolor=_C_RECEIVER, label='receiver',           edgecolor=_C_EDGE),
            mpatches.Patch(facecolor=_C_BOTH,     label='driver + receiver',  edgecolor=_C_EDGE),
        ]
        ax.legend(handles=handles, loc='upper right', fontsize=9, framealpha=0.85)
        self._canvas.draw_idle()

    def _on_canvas_click(self, event):
        if event.inaxes != self._ax:
            return
        # Ignore while zoom/pan toolbar is active
        tb = getattr(self._canvas, 'toolbar', None)
        if tb and getattr(tb, 'mode', '') != '':
            return

        for patch, inst in list(self._patch_inst.items()):
            if patch.contains(event)[0]:
                # Select this inst in the inst listbox and trigger expansion
                if inst in self._inst_items:
                    idx = self._inst_items.index(inst)
                    self._inst_lb.selection_clear(0, tk.END)
                    self._inst_lb.selection_set(idx)
                    self._inst_lb.see(idx)
                self._on_inst_select()
                return

    def _on_hover(self, event):
        if event.inaxes != self._ax:
            self._clear_hover()
            return

        for patch, inst in list(self._patch_inst.items()):
            if patch.contains(event)[0]:
                info = self.inst_info.get(inst, {})
                nets = sorted(self.inst_nets.get(inst, set()))
                tip = f'{inst}\n{info.get("cell","")}\nnets: {", ".join(nets[:4])}'
                if len(nets) > 4:
                    tip += f' +{len(nets)-4}'

                if self._hover_ann is not None:
                    try:
                        self._hover_ann.remove()
                    except Exception:
                        pass
                self._hover_ann = self._ax.annotate(
                    tip,
                    xy=(event.xdata, event.ydata),
                    xytext=(12, 12), textcoords='offset points',
                    fontsize=7.5,
                    bbox=dict(boxstyle='round,pad=0.4', fc='lightyellow',
                              ec='#aaaaaa', alpha=0.95),
                    zorder=30)
                self._canvas.draw_idle()
                return

        self._clear_hover()

    def _clear_hover(self):
        if self._hover_ann is not None:
            try:
                self._hover_ann.remove()
            except Exception:
                pass
            self._hover_ann = None
            self._canvas.draw_idle()


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    # Parse --ipc <name> flag (consume before passing remaining args to app)
    args = sys.argv[1:]
    ipc_name = None
    if '--ipc' in args:
        idx = args.index('--ipc')
        if idx + 1 < len(args):
            ipc_name = args[idx + 1]
            args = args[:idx] + args[idx + 2:]
        else:
            args = args[:idx]

    root = tk.Tk()
    # Attempt HiDPI scaling on Mac/Windows
    try:
        root.tk.call('tk', 'scaling', 2.0)
    except Exception:
        pass

    app = DefViz(root)

    if len(args) >= 1:
        app._def_var.set(os.path.abspath(args[0]))
    if len(args) >= 2:
        app._lef_var.set(os.path.abspath(args[1]))
    if len(args) >= 1 and os.path.exists(args[0]):
        root.after(200, app._load)
        if ipc_name is None:
            ipc_name = os.path.splitext(os.path.basename(args[0]))[0]

    if ipc_name:
        from viz_ipc import VizIPC, POLL_MS
        ipc = VizIPC(ipc_name)
        ipc.on_message = app._on_ipc_message
        ipc.connect_or_serve()
        app._ipc = ipc
        print(f'[def_viz] IPC session={ipc_name!r} connected={ipc._connected}')

        def _ipc_tick():
            try:
                ipc.poll()
            except Exception as exc:
                print(f'[def_viz] _ipc_tick error: {exc}')
            root.after(POLL_MS, _ipc_tick)
        root.after(POLL_MS, _ipc_tick)

    root.lift()
    root.focus_force()
    root.mainloop()


if __name__ == '__main__':
    main()
