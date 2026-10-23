"""
Desktop UI for the multi-rule watchlist breakout monitor.

Run: python DailyTrade_app.py
"""

import sys
import json
import queue
import threading
import tkinter as tk
from tkinter import ttk, simpledialog
from datetime import timedelta
from pathlib import Path

import monitor_engine as engine

if getattr(sys, 'frozen', False):
    # Running as a PyInstaller exe: __file__ would resolve to the temp
    # extraction folder instead of where the exe actually lives. theme.py
    # is bundled directly into the exe (see DailyTrade.spec's pathex), so
    # no sys.path manipulation is needed in this branch.
    APP_DIR = Path(sys.executable).resolve().parent
else:
    APP_DIR = Path(__file__).resolve().parent
    sys.path.insert(0, str(APP_DIR.parent / 'UIDesign' / 'ios16_ui_kit'))
import theme

CONFIG_PATH = APP_DIR / 'DailyTrade_config.json'

RULE_ROW_H = 48
GREEN_TINT = '#E3F9E5'


class App:
    def __init__(self, root):
        self.root = root
        root.title('DailyTrade')
        root.geometry('600x860')
        root.minsize(540, 640)
        root.configure(bg=theme.COLOR_BG)

        self._init_style()

        self.ui_queue = queue.Queue()
        self.ctx = None
        self.stop_event = threading.Event()
        self.worker = None
        self.running = False
        self.rules = engine.default_rules()
        self.selected_group = tk.StringVar(value='')
        self.all_groups = []
        self.logs_visible = True
        self.rule_state = {r.id: {'expanded': False} for r in self.rules}
        self.log_range = 'today'
        self.last_widget = None

        self._load_config()
        self._build_widgets()
        self.root.protocol('WM_DELETE_WINDOW', self._on_close)
        self._connect_and_load()
        self.root.after(150, self._poll_queue)
        self.root.after(5000, self._auto_refresh_logs)

    # --------------------------------------------------------- style
    def _init_style(self):
        style = ttk.Style()
        try:
            style.theme_use('clam')
        except tk.TclError:
            pass
        style.configure('Treeview', background=theme.COLOR_CARD, fieldbackground=theme.COLOR_CARD,
                         foreground=theme.COLOR_LABEL, font=theme.FONT_BODY, rowheight=24, borderwidth=0)
        style.configure('Treeview.Heading', font=theme.FONT_SECTION_LABEL, background=theme.COLOR_BG,
                         foreground=theme.COLOR_SECONDARY_LABEL, borderwidth=0, relief='flat')
        style.map('Treeview.Heading', background=[('active', theme.COLOR_BG)])
        style.map('Treeview', background=[('selected', theme.COLOR_BLUE)], foreground=[('selected', 'white')])
        style.layout('Treeview', [('Treeview.treearea', {'sticky': 'nswe'})])

    # --------------------------------------------------------- config
    def _load_config(self):
        if not CONFIG_PATH.exists():
            return
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            return
        self.selected_group.set(data.get('selected_group', ''))
        saved_rules = data.get('rules', {})
        for rule in self.rules:
            saved = saved_rules.get(rule.id)
            if saved:
                rule.name = saved.get('name', rule.name)
                rule.enabled = saved.get('enabled', rule.enabled)

    def _save_config(self):
        data = {
            'selected_group': self.selected_group.get(),
            'rules': {r.id: {'name': r.name, 'enabled': r.enabled} for r in self.rules},
        }
        try:
            CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding='utf-8')
        except OSError:
            pass

    # ------------------------------------------------------------ UI
    def _pack_next(self, widget, **kwargs):
        if self.last_widget is not None:
            kwargs['after'] = self.last_widget
        widget.pack(**kwargs)
        self.last_widget = widget

    def _section_label(self, text, subtitle=None):
        lbl = tk.Label(self.root, text=text.upper(), font=theme.FONT_SECTION_LABEL,
                        fg=theme.COLOR_SECONDARY_LABEL, bg=theme.COLOR_BG, anchor='w')
        self._pack_next(lbl, fill='x', padx=24, pady=(16, 6))
        return lbl

    def _build_widgets(self):
        # Header
        header = tk.Frame(self.root, bg=theme.COLOR_BG)
        self._pack_next(header, fill='x', padx=20, pady=(20, 4))
        tk.Label(header, text='DailyTrade', font=theme.FONT_LARGE_TITLE,
                 fg=theme.COLOR_LABEL, bg=theme.COLOR_BG).pack(anchor='w')
        tk.Label(header, text='Premarket breakout monitor', font=theme.FONT_BODY,
                 fg=theme.COLOR_SECONDARY_LABEL, bg=theme.COLOR_BG).pack(anchor='w', pady=(2, 0))

        # Watchlist chips card
        self._section_label('Watchlist')
        self.wl_canvas = tk.Canvas(self.root, bg=theme.COLOR_BG, highlightthickness=0, height=56)
        self._pack_next(self.wl_canvas, fill='x', padx=16)
        self.wl_canvas.bind('<Configure>', lambda e: self._redraw_watchlist_chips())

        refresh_row = tk.Frame(self.root, bg=theme.COLOR_BG)
        self._pack_next(refresh_row, fill='x', padx=24, pady=(4, 0))
        refresh_lbl = tk.Label(refresh_row, text='Refresh', font=theme.FONT_CAPTION,
                                fg=theme.COLOR_BLUE, bg=theme.COLOR_BG, cursor='hand2')
        refresh_lbl.pack(side='left')
        refresh_lbl.bind('<Button-1>', lambda e: self._refresh_list())

        # Candidate list card
        self._section_label('Candidates')
        tree_card = tk.Frame(self.root, bg=theme.COLOR_CARD)
        self._pack_next(tree_card, fill='both', expand=False, padx=16)
        tree_inner = tk.Frame(tree_card, bg=theme.COLOR_CARD)
        tree_inner.pack(fill='both', expand=True, padx=1, pady=1)
        self.tree = ttk.Treeview(tree_inner, columns=('list',), show='tree headings', height=7)
        self.tree.heading('#0', text='Name')
        self.tree.heading('list', text='List Name')
        self.tree.column('#0', width=180, stretch=False)
        self.tree.column('list', width=260, stretch=True)
        self.tree.tag_configure('candidate', background=GREEN_TINT)
        self.tree.pack(side='left', fill='both', expand=True)
        sb = ttk.Scrollbar(tree_inner, orient='vertical', command=self.tree.yview)
        sb.pack(side='right', fill='y')
        self.tree.configure(yscrollcommand=sb.set)

        # Status
        self.status_var = tk.StringVar(value='Connecting to OpenD...')
        status_lbl = tk.Label(self.root, textvariable=self.status_var, font=theme.FONT_CAPTION,
                               fg=theme.COLOR_SECONDARY_LABEL, bg=theme.COLOR_BG,
                               wraplength=560, justify='left', anchor='w')
        self._pack_next(status_lbl, fill='x', padx=24, pady=(10, 4))

        # Start/Stop button
        self.toggle_canvas = tk.Canvas(self.root, bg=theme.COLOR_BG, highlightthickness=0, height=52)
        self._pack_next(self.toggle_canvas, fill='x', padx=16, pady=(6, 4))
        self.toggle_canvas.bind('<Configure>', lambda e: self._redraw_toggle_button())
        self.toggle_canvas.bind('<Button-1>', lambda e: self._on_toggle())

        # Rules card (one canvas, all rows drawn on it)
        self._section_label('Rules')
        self.rules_canvas = tk.Canvas(self.root, bg=theme.COLOR_BG, highlightthickness=0)
        self._pack_next(self.rules_canvas, fill='x', padx=16)
        self.rules_canvas.bind('<Configure>', lambda e: self._redraw_rules_card())

        # Logs section
        log_header = tk.Frame(self.root, bg=theme.COLOR_BG)
        self._pack_next(log_header, fill='x', padx=20, pady=(16, 6))
        self.log_toggle_lbl = tk.Label(log_header, text='▾  LOGS', font=theme.FONT_SECTION_LABEL,
                                        fg=theme.COLOR_SECONDARY_LABEL, bg=theme.COLOR_BG, cursor='hand2')
        self.log_toggle_lbl.pack(side='left')
        self.log_toggle_lbl.bind('<Button-1>', lambda e: self._toggle_logs())

        self.log_body = tk.Frame(self.root, bg=theme.COLOR_BG)
        self._pack_next(self.log_body, fill='both', expand=True, padx=16, pady=(0, 16))

        self.log_range_canvas = tk.Canvas(self.log_body, bg=theme.COLOR_BG, highlightthickness=0, height=30)
        self.log_range_canvas.pack(fill='x')
        self.log_range_canvas.bind('<Configure>', lambda e: self._redraw_log_range_chips())

        log_tree_card = tk.Frame(self.log_body, bg=theme.COLOR_CARD)
        log_tree_card.pack(fill='both', expand=True, pady=(8, 0))
        log_tree_inner = tk.Frame(log_tree_card, bg=theme.COLOR_CARD)
        log_tree_inner.pack(fill='both', expand=True, padx=1, pady=1)
        self.log_tree = ttk.Treeview(
            log_tree_inner, columns=('time', 'symbol', 'rule'), show='headings', height=8)
        self.log_tree.heading('time', text='Time (ET)')
        self.log_tree.heading('symbol', text='Symbol')
        self.log_tree.heading('rule', text='Rule Triggered')
        self.log_tree.column('time', width=150, stretch=False)
        self.log_tree.column('symbol', width=90, stretch=False)
        self.log_tree.column('rule', width=260, stretch=True)
        self.log_tree.pack(side='left', fill='both', expand=True)
        log_sb = ttk.Scrollbar(log_tree_inner, orient='vertical', command=self.log_tree.yview)
        log_sb.pack(side='right', fill='y')
        self.log_tree.configure(yscrollcommand=log_sb.set)

        self._redraw_rules_card()
        self._redraw_toggle_button()
        self._redraw_log_range_chips()
        self._refresh_logs()

    # ---------------------------------------------------- watchlist chips
    def _redraw_watchlist_chips(self):
        h = theme.layout_chips(
            self.wl_canvas, self.all_groups, get_label=lambda g: g,
            is_selected=lambda g: g == self.selected_group.get(),
            on_click=self._on_chip_selected,
        )
        self.wl_canvas.config(height=max(h, theme.CHIP_HEIGHT))

    def _on_chip_selected(self, group_name):
        if group_name == self.selected_group.get():
            return
        self.selected_group.set(group_name)
        self._redraw_watchlist_chips()
        self._save_config()
        self._load_list_for_group(group_name)

    # ------------------------------------------------------------ toggle
    def _redraw_toggle_button(self):
        c = self.toggle_canvas
        c.delete('all')
        w = c.winfo_width() or 560
        h = 52
        if self.running:
            fill = theme.COLOR_RED
            label = '■  Stop Monitor'
        else:
            fill = theme.COLOR_GREEN
            label = '▶  Start Daily Monitor'
        theme.rounded_rect(c, 0, 0, w, h, theme.RADIUS_BUTTON, fill=fill, outline='')
        c.create_text(w / 2, h / 2, text=label, font=theme.FONT_TITLE, fill='white')

    # -------------------------------------------------------- rule rows
    def _redraw_rules_card(self):
        c = self.rules_canvas
        c.delete('all')
        w = c.winfo_width() or 560
        pad = 14
        y = 0
        row_bottoms = []
        for idx, rule in enumerate(self.rules):
            row_top = y
            color = theme.CATEGORY_COLORS[idx % len(theme.CATEGORY_COLORS)]
            theme.icon_badge(c, pad, row_top + 9, 30, color, str(idx + 1))

            name_x = pad + 30 + 10
            toggle_x = w - pad - theme.TOGGLE_WIDTH
            edit_x = toggle_x - 30
            chev_x = edit_x - 24

            name_id = c.create_text(name_x, row_top + RULE_ROW_H / 2, text=rule.name,
                                     font=theme.FONT_TITLE, fill=theme.COLOR_LABEL, anchor='w')
            expanded = self.rule_state[rule.id]['expanded']
            chev_id = theme.chevron(c, chev_x, row_top + RULE_ROW_H / 2 - 4, expanded, size=9)
            for iid in (name_id, chev_id):
                c.addtag_withtag(f'expand_{rule.id}', iid)
            # widen the clickable zone to the full name area
            hit = c.create_rectangle(name_x - 4, row_top, chev_x + 12, row_top + RULE_ROW_H,
                                      fill='', outline='')
            c.addtag_withtag(f'expand_{rule.id}', hit)

            edit_id = c.create_text(edit_x, row_top + RULE_ROW_H / 2, text='✏️',
                                     font=('Segoe UI Emoji', 11))
            c.addtag_withtag(f'edit_{rule.id}', edit_id)

            pill, knob = theme.pill_toggle(c, toggle_x, row_top + (RULE_ROW_H - theme.TOGGLE_HEIGHT) / 2,
                                            rule.enabled)
            c.addtag_withtag(f'toggle_{rule.id}', pill)
            c.addtag_withtag(f'toggle_{rule.id}', knob)

            y = row_top + RULE_ROW_H
            if expanded:
                desc_id = c.create_text(name_x, y + 6, text=rule.description, font=theme.FONT_CAPTION,
                                         fill=theme.COLOR_SECONDARY_LABEL, anchor='nw', width=w - name_x - pad)
                bbox = c.bbox(desc_id)
                desc_h = (bbox[3] - bbox[1] + 12) if bbox else 0
                y += desc_h

            row_bottoms.append(y)
            c.tag_bind(f'expand_{rule.id}', '<Button-1>', lambda e, r=rule: self._toggle_rule_details(r))
            c.tag_bind(f'edit_{rule.id}', '<Button-1>', lambda e, r=rule: self._rename_rule(r))
            c.tag_bind(f'toggle_{rule.id}', '<Button-1>', lambda e, r=rule: self._toggle_rule(r))

            if idx < len(self.rules) - 1:
                c.create_line(pad, y - 0.5, w - pad, y - 0.5, fill=theme.COLOR_SEPARATOR)

        total_h = row_bottoms[-1] if row_bottoms else 0
        bg_id = theme.rounded_rect(c, 0, 0, w, total_h, theme.RADIUS_CARD, fill=theme.COLOR_CARD, outline='')
        c.tag_lower(bg_id)  # push the card background behind the rows just drawn
        c.config(height=total_h)

    def _toggle_rule(self, rule):
        rule.enabled = not rule.enabled
        self._redraw_rules_card()
        self._save_config()

    def _rename_rule(self, rule):
        new_name = simpledialog.askstring('Rename Rule', 'Rule name:', initialvalue=rule.name, parent=self.root)
        if new_name and new_name.strip():
            rule.name = new_name.strip()
            self._redraw_rules_card()
            self._save_config()

    def _toggle_rule_details(self, rule):
        st = self.rule_state[rule.id]
        st['expanded'] = not st['expanded']
        self._redraw_rules_card()

    # ------------------------------------------------------- connection
    def _connect_and_load(self):
        def worker():
            try:
                self.ctx = engine.connect(timeout_seconds=60,
                                           poll_cb=lambda m: self.ui_queue.put(('status', m)))
                self.ui_queue.put(('status', 'Connected. Loading custom watchlists...'))
                groups = engine.get_custom_groups(self.ctx)
                self.ui_queue.put(('groups', groups))
            except engine.EngineError as e:
                self.ui_queue.put(('status', f'Error: {e}'))
        threading.Thread(target=worker, daemon=True).start()

    def _on_groups_loaded(self, groups):
        self.all_groups = groups
        self._redraw_watchlist_chips()
        if not groups:
            self.status_var.set('Connected, but no custom watchlists found in Moomoo.')
            return
        current = self.selected_group.get()
        if current not in groups:
            current = groups[0]
            self.selected_group.set(current)
            self._redraw_watchlist_chips()
        self._load_list_for_group(current)

    def _load_list_for_group(self, group_name):
        if not self.ctx or not group_name:
            return

        def worker():
            try:
                codes = engine.get_group_members(self.ctx, group_name)
                rows = [{'code': c, 'name': engine.to_symbol(c), 'group_name': group_name, 'is_candidate': False}
                        for c in codes]
                self.ui_queue.put(('candidates', rows))
                self.ui_queue.put(('status', f'Ready. {len(codes)} stock(s) in "{group_name}".'
                                              if codes else f'"{group_name}" is empty.'))
            except engine.EngineError as e:
                self.ui_queue.put(('status', f'Error: {e}'))
        threading.Thread(target=worker, daemon=True).start()

    def _refresh_list(self):
        if not self.ctx:
            self.status_var.set('Not connected to OpenD yet.')
            return
        self._load_list_for_group(self.selected_group.get())

    # ------------------------------------------------------- start/stop
    def _on_toggle(self):
        if self.running:
            self._stop_monitor()
        else:
            self._start_monitor()

    def _start_monitor(self):
        group_name = self.selected_group.get()
        if not self.ctx or not group_name:
            self.status_var.set('Not connected, or no watchlist selected.')
            return
        self.running = True
        self.stop_event.clear()
        self._redraw_toggle_button()

        def worker():
            engine.run_daily_cycle(
                self.ctx, group_name, self.rules, self.stop_event,
                candidates_cb=lambda rows: self.ui_queue.put(('candidates', rows)),
                status_cb=lambda m: self.ui_queue.put(('status', m)),
                alert_cb=lambda code, rule_name, price, ref: self.ui_queue.put(
                    ('status', f'ALERT: {engine.to_symbol(code)} triggered "{rule_name}" at {price:.2f}')),
            )
            self.ui_queue.put(('finished', None))

        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()

    def _stop_monitor(self):
        self.stop_event.set()
        self.status_var.set('Stopping...')

    # ---------------------------------------------------------------- logs
    def _toggle_logs(self):
        self.logs_visible = not self.logs_visible
        if self.logs_visible:
            self.log_body.pack(fill='both', expand=True, padx=16, pady=(0, 16))
            self.log_toggle_lbl.config(text='▾  LOGS')
            self._refresh_logs()
        else:
            self.log_body.pack_forget()
            self.log_toggle_lbl.config(text='▸  LOGS')

    def _redraw_log_range_chips(self):
        options = [('today', 'Today'), ('yesterday', 'Yesterday'), ('3days', 'Last 3 Days')]
        h = theme.layout_chips(
            self.log_range_canvas, options, get_label=lambda o: o[1],
            is_selected=lambda o: o[0] == self.log_range,
            on_click=self._on_log_range_selected,
        )
        self.log_range_canvas.config(height=max(h, theme.CHIP_HEIGHT))

    def _on_log_range_selected(self, option):
        self.log_range = option[0]
        self._redraw_log_range_chips()
        self._refresh_logs()

    def _refresh_logs(self):
        if not self.logs_visible:
            return
        today = engine.now_et().date()
        if self.log_range == 'today':
            dates = [today]
        elif self.log_range == 'yesterday':
            dates = [today - timedelta(days=1)]
        else:
            dates = [today - timedelta(days=i) for i in range(2, -1, -1)]

        events = []
        for d in dates:
            events.extend(engine.read_alert_events(str(APP_DIR), d))
        events.sort(key=lambda e: e['time'])

        self.log_tree.delete(*self.log_tree.get_children())
        for e in events:
            self.log_tree.insert('', 'end', values=(e['time'], e['symbol'], e['rule']))
        if events:
            children = self.log_tree.get_children()
            if children:
                self.log_tree.see(children[-1])

    def _auto_refresh_logs(self):
        self._refresh_logs()
        self.root.after(5000, self._auto_refresh_logs)

    # ------------------------------------------------------------- queue
    def _poll_queue(self):
        try:
            while True:
                kind, payload = self.ui_queue.get_nowait()
                if kind == 'status':
                    self.status_var.set(payload)
                elif kind == 'candidates':
                    self._update_tree(payload)
                elif kind == 'groups':
                    self._on_groups_loaded(payload)
                elif kind == 'finished':
                    self.running = False
                    self._redraw_toggle_button()
        except queue.Empty:
            pass
        self.root.after(150, self._poll_queue)

    def _update_tree(self, rows):
        self.tree.delete(*self.tree.get_children())
        for r in rows:
            tags = ('candidate',) if r.get('is_candidate') else ()
            self.tree.insert('', 'end', iid=r['code'], text=r['name'], values=(r['group_name'],), tags=tags)

    # ------------------------------------------------------------- close
    def _on_close(self):
        self.stop_event.set()
        if self.worker and self.worker.is_alive():
            self.worker.join(timeout=3)
        if self.ctx:
            try:
                self.ctx.close()
            except Exception:
                pass
        self.root.destroy()


def main():
    engine.configure_logging(str(APP_DIR))
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == '__main__':
    main()
