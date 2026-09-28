"""Run with python3 -m tui. Standard-library curses, macOS/Linux."""

import argparse
import curses
import textwrap
import time
from queue import Empty, SimpleQueue
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from .registry import agent_tabs


class TerminalApp:
    def __init__(self, tabs):
        self.tabs, self.tab_index, self.selected, self.scroll = tabs, 0, 0, 0
        self.notice = "Ready. Choose an action above."
        self.pending = None
        self.clicks = []
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.job = None
        self.events = SimpleQueue()
        self.activity = []
        self.show_activity = False
        self.show_noise = False
        self.show_analysis = False
        self.show_backlog = False
        self.reminder_notice = ""
        self.started_at = None
        self.stage_at = None
        self.rendered_selection = None

    def submit(self, action, identifier, payload=None, tab=None, background=False):
        self.job_tab = tab or self.tab
        self.background_job = background
        self.started_at = self.stage_at = time.monotonic()
        self.job_action = action
        self.activity = []
        self.notice = {"c": "Starting Gmail connection…", "s": "Starting inbox scan…",
                       "t": "Analyzing subjects and senders…", "r": "Refreshing saved proposals…", "a": "Recording your approval…",
                       "x": "Recording your rejection…", "e": "Checking approved action…",
                       "n": "Resetting the offline demo…"}.get(action, "Working…")
        self.notice = getattr(self.job_tab, "action_messages", {}).get(action, self.notice)
        self.events.put((self.started_at, self.notice))

        def progress(message):
            self.events.put((time.monotonic(), message))

        self.job = self.executor.submit(self.job_tab.run, action, identifier, progress=progress,
                                        **({"payload": payload} if payload is not None else {}))

    def collect_progress(self):
        if hasattr(self.tab, "consume_updates"):
            old_rows = self.rows()
            selected_id = self.rendered_selection or (old_rows[self.selected][0] if self.selected < len(old_rows) else None)
            changed = self.tab.consume_updates()
            # New urgent results may sort above the email currently being read.
            # Keep that email selected rather than jumping to a different one.
            if changed and selected_id is not None:
                self.selected = next((i for i, row in enumerate(self.rows()) if row[0] == selected_id), 0)
        for tab in self.tabs:
            if tab is not self.tab and hasattr(tab, "consume_updates"):
                tab.consume_updates()
        while True:
            try:
                when, message = self.events.get_nowait()
            except Empty:
                break
            self.stage_at, self.notice = when, message
            elapsed = when - self.started_at if self.started_at is not None else 0
            self.activity.append(f"{elapsed:5.1f}s  {message}")
            self.activity = self.activity[-100:]
        if self.job is not None and self.job.done():
            # The worker has finished; drain its final progress before the result.
            if not self.events.empty():
                self.collect_progress()
                return
            try:
                self.notice = self.job.result()
                if self.job_action == "t" and not self.background_job and self.job_tab is self.tab:
                    self.select_view("analysis")
                elif self.job_action == "c" and not self.background_job and self.job_tab is self.tab:
                    self.select_view("signal")
                elif self.job_action in ("f", "r") and not self.background_job and self.job_tab is self.tab:
                    self.select_view("signal")
                    planned = getattr(self.tab, "last_planned_ids", [])
                    if planned:
                        self.selected = next((i for i, row in enumerate(self.rows()) if row[0] == 'p:' + planned[0]), 0)

            except Exception as error:
                self.notice = f"Error: {error}"
                try:
                    self.job_tab.refresh()
                except Exception:
                    pass  # Preserve the original error if refresh also fails.
            self.activity.append(f"{time.monotonic() - self.started_at:5.1f}s  {self.notice}")
            self.job = None

    def activity_lines(self):
        lines = ["BACKGROUND ACTIVITY", ""]
        if self.job is not None:
            now = time.monotonic()
            lines += [f"Running for {now - self.started_at:.0f}s · current step {now - self.stage_at:.0f}s",
                      "", self.notice, ""]
        scan = getattr(self.tab, "last_scan", None) or {}
        if scan.get("timings"):
            lines += ["LAST SCAN TIMINGS"]
            lines += [f"{stage}: {value['seconds']:.1f}s / {value['calls']} operations"
                      for stage, value in scan["timings"].items()]
            lines += [""]
        analysis = getattr(self.tab, "title_analysis", {})
        if analysis.get("timings"):
            lines += ["LAST ANALYZE TIMINGS"]
            lines += [f"{stage}: {seconds:.1f}s" for stage, seconds in analysis["timings"].items()]
            lines += [""]
        lines += self.activity or ["No activity yet. Choose an action above."]
        return lines

    def rows(self):
        return self.tab.rows("backlog" if self.show_backlog else "analysis" if self.show_analysis else "activity" if self.show_activity else "noise" if self.show_noise else "signal")

    def select_view(self, view):
        self.show_activity, self.show_noise = view == "activity", view == "noise"
        self.show_analysis = view == "analysis"
        self.show_backlog = view == "backlog"
        self.selected, self.scroll = 0, 0
        self.rendered_selection = None

    @property
    def tab(self):
        return self.tabs[self.tab_index]

    def write(self, screen, y, x, text, style=0):
        height, width = screen.getmaxyx()
        if 0 <= y < height and 0 <= x < width - 1:
            # Strip terminal control characters from provider-supplied text.
            safe = "".join(c if c.isprintable() else " " for c in str(text))
            screen.addnstr(y, x, safe, width - x - 1, style)

    def draw(self, screen):
        screen.erase()
        height, width = screen.getmaxyx()
        self.clicks = []
        if height < 20 or width < 78:
            self.write(screen, 0, 0, "Resize terminal to at least 78 columns × 20 rows. Q quits.")
            screen.refresh()
            return
        x = 2
        for index, tab in enumerate(self.tabs):
            label = f" {tab.name} "
            self.write(screen, 0, x, label, curses.A_REVERSE if index == self.tab_index else curses.A_DIM)
            self.clicks.append((0, x, x + len(label), ("tab", index)))
            x += len(label) + 2
        if getattr(self.tab, "is_chat", False):
            self.draw_chat(screen, height, width)
            return
        x = 2
        for key, label in self.tab.toolbar:
            button = f"[ {label} ({key.upper()}) ]"
            self.write(screen, 2, x, button, curses.A_BOLD)
            self.clicks.append((2, x, x + len(button), ("action", key)))
            x += len(button) + 2
        account = getattr(self.tab, "connection", getattr(self.tab, "gmail", None))
        status = account.account if account and self.tab.live else "" if self.tab.live else "Demo"
        self.write(screen, 2, x + 1, status, curses.A_DIM)
        if self.tab.live and account is None and not self.show_activity:
            self.write(screen, 5, 2, getattr(self.tab, "connection_label", "Gmail") + " is not connected.")
            if self.job is not None or self.notice.startswith("Error:"):
                self.write(screen, height - 3, 2, self.notice)
            if self.reminder_notice:
                self.write(screen, height - 4, 2, "Calendar: " + self.reminder_notice + " · 0 dismiss", curses.A_BOLD)
            self.write(screen, height - 2, 2, "C Connect · Q Quit", curses.A_DIM)
            screen.refresh()
            return
        split = min(55, width // 2) if self.show_analysis or getattr(self.tab, "wide_list", False) else min(43, width // 3)
        self.write(screen, 4, 2, "Past week" if self.show_backlog else "Filters" if self.show_analysis else "Activity" if self.show_activity else "Noise" if self.show_noise else "Signal", curses.A_BOLD)
        received, scan_scope = self.tab.timeframe()
        if self.show_analysis:
            analysis = self.tab.title_analysis
            received = f"{len(analysis.get('sample', []))} subjects and senders"
            scan_scope = "Mark each choice: + Include · - Exclude · U Undecided · Enter Save · M Manual · Esc Signal"

        self.write(screen, 4, 11, received, curses.A_DIM)
        self.write(screen, 5, 2, scan_scope, curses.A_DIM)
        rows = self.rows()
        self.selected = min(self.selected, max(0, len(rows) - 1))
        visible = height - (11 if self.reminder_notice else 10)
        start = max(0, self.selected - visible + 1)
        for offset, (identifier, label) in enumerate(rows[start:start + visible]):
            index = start + offset
            self.write(screen, 7 + offset, 2, label[:split - 4],
                       curses.A_REVERSE if index == self.selected else 0)
            self.clicks.append((7 + offset, 2, split, ("row", index)))
        if not rows:
            self.write(screen, 7, 2, "No topics or senders found." if self.show_analysis else getattr(self.tab, "empty_label", "No emails to review."), curses.A_DIM)
        identifier = rows[self.selected][0] if rows else None
        self.rendered_selection = identifier
        lines = []
        if self.show_analysis:
            content = self.tab.analysis_details(identifier)
        elif self.show_activity:
            content = (self.tab.audit_details(identifier) if identifier else []) + ["", "RECENT ACTIVITY"] + self.activity_lines()
        else:
            content = self.tab.details(identifier)
        for line in content:
            for paragraph in line.split("\n"):
                lines.extend(textwrap.wrap(paragraph, max(10, width - split - 5)) or [""])
        self.scroll = min(self.scroll, max(0, len(lines) - visible))
        for offset, line in enumerate(lines[self.scroll:self.scroll + visible]):
            y = 7 + offset
            self.write(screen, y, split + 2, line)
            if not self.show_activity and identifier and self.scroll + offset == len(lines) - 1:
                for label, key in (("Approve [A]", "a"), ("Reject [X]", "x"), ("Execute [E]", "e")):
                    position = line.find(label)
                    if position >= 0:
                        left = split + 2 + position
                        self.clicks.append((y, left, left + len(label), ("action", key)))
        for y in range(6, height - 3):
            self.write(screen, y, split, "│", curses.A_DIM)
        if self.pending:
            self.write(screen, height - 3, 2, self.pending[2], curses.A_BOLD)
            self.write(screen, height - 2, 2, "Y confirms · any other key cancels", curses.A_REVERSE)
        else:
            if self.job is not None:
                now = time.monotonic()
                spinner = "|/-\\"[int(now * 4) % 4]
                self.write(screen, height - 3, 2,
                           f"{spinner} {self.notice}  ({now - self.started_at:.0f}s)" if self.show_activity else
                           f"{spinner} {'Connecting Gmail' if self.job_action == 'c' else 'Analyzing titles' if self.job_action == 't' else 'Scanning' if self.job_action == 's' else 'Saving'}… {now - self.started_at:.0f}s")
            else:
                self.write(screen, height - 3, 2, self.notice)
                self.write(screen, height - 2, 2,
                           "↑↓ Select · + Include · - Exclude · U Clear · Enter Save · M Manual · Esc Back" if self.show_analysis else
                           getattr(self.tab, "footer", "↑↓ Select · PgUp/PgDn Read · T Analyze · L Activity · I Filters · Q Quit"), curses.A_DIM)
        if self.reminder_notice:
            screen.move(height - 4, 0)
            screen.clrtoeol()
            self.write(screen, height - 4, 2, "Calendar: " + self.reminder_notice + " · 0 dismiss", curses.A_BOLD)
        screen.refresh()

    def draw_chat(self, screen, height, width):
        tab = self.tab
        x = 2
        for key, label in tab.toolbar:
            button = f"[ {label} ]"
            self.write(screen, 2, x, button, curses.A_BOLD)
            self.clicks.append((2, x, x + len(button), ("action", key)))
            x += len(button) + 2
        self.write(screen, 3, 2, tab.status(), curses.A_DIM)
        self.write(screen, 4, 2, "Plain-language requests · /help · proposals require your approval", curses.A_DIM)
        lines = []
        for entry in tab.messages:
            prefix = {"user": "You", "assistant": "Agent", "review": "Review"}.get(entry['role'], entry['role'])
            lines += [prefix + ":"]
            for paragraph in entry['content'].split('\n'):
                lines += textwrap.wrap(paragraph, width - 6) or ['']
            lines += ['']
        available = max(1, height - 12)
        tab.chat_scroll = min(tab.chat_scroll, max(0, len(lines) - available))
        end = max(0, len(lines) - tab.chat_scroll)
        for row, line in enumerate(lines[max(0, end - available):end]):
            self.write(screen, 6 + row, 2, line)
        if self.job is not None:
            self.write(screen, height - 6, 2, self.notice, curses.A_DIM)
        else:
            self.write(screen, height - 6, 2, "Enter sends · PgUp/PgDn history · Tab switches agents · Ctrl-Q quits", curses.A_DIM)
        if self.reminder_notice:
            self.write(screen, height - 5, 2, "Calendar: " + self.reminder_notice, curses.A_BOLD)
        # Keep the caret visible for long requests; editing never triggers agent hotkeys.
        span = width - 6
        start = max(0, tab.cursor - span + 1)
        visible_input = tab.input[start:start + span]
        self.write(screen, height - 3, 2, "> " + visible_input)
        caret = tab.cursor - start
        self.write(screen, height - 2, 4 + caret, "^", curses.A_DIM)
        self.write(screen, height - 1, 2, "Esc Gmail · /approve 1 then /execute 1 · /help", curses.A_DIM)
        screen.refresh()

    def chat_key(self, key, screen):
        tab = self.tab
        if key in (9, "\t", curses.KEY_BTAB, curses.KEY_MOUSE):
            return False
        if key in (17, "\x11"):
            return "quit"
        if key in (27, "\x1b"):
            self.tab_index = 0
            self.select_view("signal")
            return True
        if key in (10, 13, "\n", "\r", curses.KEY_ENTER):
            message = tab.input.strip()
            if message == '/quit':
                return 'quit'
            if message and self.job is None:
                tab.input, tab.cursor, tab.chat_scroll = '', 0, 0
                self.submit('chat', None, payload={'message': message})
            elif message:
                self.notice = 'Still working; your next message is kept in the input.'
            return True
        if key in (curses.KEY_PPAGE, curses.KEY_UP):
            tab.chat_scroll += max(1, screen.getmaxyx()[0] - 12) if key == curses.KEY_PPAGE else 3
        elif key in (curses.KEY_NPAGE, curses.KEY_DOWN):
            tab.chat_scroll = max(0, tab.chat_scroll - (max(1, screen.getmaxyx()[0] - 12) if key == curses.KEY_NPAGE else 3))
        elif key == curses.KEY_LEFT:
            tab.cursor = max(0, tab.cursor - 1)
        elif key == curses.KEY_RIGHT:
            tab.cursor = min(len(tab.input), tab.cursor + 1)
        elif key in (curses.KEY_HOME, "\x01"):
            tab.cursor = 0
        elif key in (curses.KEY_END, "\x05"):
            tab.cursor = len(tab.input)
        elif key in ("\x7f", "\b", 127, 8, curses.KEY_BACKSPACE):
            if tab.cursor:
                tab.input = tab.input[:tab.cursor-1] + tab.input[tab.cursor:]
                tab.cursor -= 1
        elif key == curses.KEY_DC:
            tab.input = tab.input[:tab.cursor] + tab.input[tab.cursor+1:]
        elif key == "\x15":
            tab.input, tab.cursor = '', 0
        elif isinstance(key, str) and key.isprintable() and len(tab.input) < 8000:
            tab.input = tab.input[:tab.cursor] + key + tab.input[tab.cursor:]
            tab.cursor += len(key)
        return True

    def action(self, key, screen=None):
        rows = self.rows()
        identifier = rows[self.selected][0] if rows else None
        if key in ("f", "r", "h") and hasattr(self.tab, "form_fields"):
            fields = self.tab.form_fields(key, identifier)
            if fields is not None:
                payload = self.edit_form(screen, fields)
                if payload is not None:
                    self.submit(key, identifier, payload=payload)
                return
        if key in ("a", "x", "e", "n"):
            self.pending = (key, identifier, self.tab.confirmation(key, identifier))
        else:
            self.submit(key, identifier)
            self.scroll = 0

    def edit_form(self, screen, fields):
        values, selected = [value for _, _, value in fields], 0
        screen.timeout(100)
        try:
            while True:
                self.tick_calendar(allow_scan=False)
                screen.erase()
                height, _ = screen.getmaxyx()
                self.write(screen, 1, 2, self.tab.name + " · " + self.tab.preferences().timezone, curses.A_BOLD)
                visible = max(1, (height - 7) // 2)
                start = max(0, selected - visible + 1)
                for row, index in enumerate(range(start, min(start + visible, len(fields)))):
                    self.write(screen, 3 + row * 2, 2, fields[index][1] + ": " + values[index],
                               curses.A_REVERSE if index == selected else 0)
                self.write(screen, height - 3, 2, "Tab/↑↓ Field · Ctrl-U Clear · Enter Submit · Esc Cancel")
                screen.refresh()
                if self.reminder_notice:
                    self.write(screen, height - 4, 2, "Calendar: " + self.reminder_notice, curses.A_BOLD)
                    screen.refresh()
                try:
                    key = screen.get_wch()
                except curses.error:
                    continue
                if key == "\x1b":
                    return None
                if key in ("\n", "\r", curses.KEY_ENTER):
                    return {name: value for (name, _, _), value in zip(fields, values)}
                if key in ("\t", curses.KEY_DOWN):
                    selected = (selected + 1) % len(fields)
                elif key in (curses.KEY_UP, curses.KEY_BTAB):
                    selected = (selected - 1) % len(fields)
                elif key == "\x15":
                    values[selected] = ""
                elif key in ("\x7f", "\b", curses.KEY_BACKSPACE):
                    values[selected] = values[selected][:-1]
                elif isinstance(key, str) and key.isprintable():
                    values[selected] += key
        finally:
            screen.timeout(100)

    def tick_calendar(self, allow_scan=True):
        for tab in self.tabs:
            if not hasattr(tab, "poll_reminders") or (self.job is not None and (self.job_tab is tab or getattr(self.job_tab, "is_chat", False))):
                continue
            try:
                due = tab.poll_reminders()
                if due:
                    self.reminder_notice = " | ".join(r['text'] for r in due)
                if allow_scan and self.job is None and tab.background_scan_due():
                    tab.next_scan = time.monotonic() + 300
                    self.submit("s", None, tab=tab, background=True)
            except Exception as error:
                self.notice = f"Calendar monitor: {error}"

    def edit_filters(self, screen):
        """Small modal editor; the main Signal view stays uncluttered."""
        fields = [("include_topics", "Include topics"), ("exclude_topics", "Exclude topics"),
                  ("include_senders", "Include senders"), ("exclude_senders", "Exclude senders")]
        values = self.tab.get_filters()
        buffers = [", ".join(values.get(name, [])) for name, _ in fields]
        selected = 0
        screen.timeout(100)
        while True:
            self.tick_calendar(allow_scan=False)
            screen.erase()
            self.write(screen, 1, 2, "Mail filters · exclusions win · sender exclusions skip whole emails")
            self.write(screen, 2, 2, "Comma-separated topics, email addresses or @domain.com. Blank = no rule.")
            for index, (_, label) in enumerate(fields):
                self.write(screen, 4 + index * 2, 2, f"{label}: {buffers[index]}",
                           curses.A_REVERSE if index == selected else 0)
            self.write(screen, 14, 2, "Tab/↑↓ Field · Enter Save · Esc Cancel")
            screen.refresh()
            try:
                key = screen.get_wch()
            except curses.error:
                continue
            if key == "\x1b":
                return
            if key in ("\n", "\r", curses.KEY_ENTER):
                self.tab.save_filters({name: [v.strip() for v in buffer.split(",") if v.strip()]
                                       for (name, _), buffer in zip(fields, buffers)})
                self.notice = "Filters saved. Scan to screen from the newest page; approvals remain unchanged."
                return
            if key in ("\t", curses.KEY_DOWN):
                selected = (selected + 1) % len(fields)
            elif key in (curses.KEY_UP, curses.KEY_BTAB):
                selected = (selected - 1) % len(fields)
            elif key in ("\x7f", "\b", curses.KEY_BACKSPACE):
                buffers[selected] = buffers[selected][:-1]
            elif isinstance(key, str) and key.isprintable():
                buffers[selected] += key

    def run(self, screen):
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        screen.keypad(True)
        screen.timeout(100)
        curses.mousemask(curses.BUTTON1_CLICKED)
        for tab in self.tabs:
            tab.refresh()
        while True:
            self.collect_progress()
            self.tick_calendar()
            self.draw(screen)
            try:
                key = screen.get_wch() if getattr(self.tab, "is_chat", False) else screen.getch()
            except curses.error:
                key = -1
            try:
                if getattr(self.tab, "is_chat", False) and key != -1:
                    handled = self.chat_key(key, screen)
                    if handled == "quit":
                        return
                    if handled:
                        continue
                    if isinstance(key, str):
                        key = ord(key)

                if key == -1:
                    continue
                if key == curses.KEY_RESIZE:
                    continue
                if key == ord('0'):
                    self.reminder_notice = ""
                    continue
                if self.pending:
                    action, identifier, _ = self.pending
                    self.pending = None
                    if key in (ord('y'), ord('Y')):
                        self.submit(action, identifier)
                    else:
                        self.notice = "Cancelled."
                    continue
                if self.show_analysis and self.job is None:
                    if key == 27:
                        self.select_view("signal")
                        continue
                    if key in (ord('+'), ord('i'), ord('I'), ord('-'), ord('x'), ord('X'), ord('u'), ord('U')):
                        rows = self.rows()
                        if rows:
                            choice = "include" if key in (ord('+'), ord('i'), ord('I')) else "exclude" if key in (ord('-'), ord('x'), ord('X')) else None
                            self.tab.mark_filter(rows[self.selected][0], choice)
                        continue
                    if key in (10, 13, curses.KEY_ENTER):
                        self.tab.save_analysis()
                        self.select_view("signal")
                        self.notice = "Filters saved. Scan now uses your choices."
                        continue
                    if key in (ord('m'), ord('M')):
                        screen.timeout(-1)
                        try:
                            self.edit_filters(screen)
                        finally:
                            screen.timeout(100)
                        continue
                    if key in (ord('a'), ord('A'), ord('e'), ord('E')):
                        continue
                if key in (ord('1'), ord('2'), ord('3')):
                    self.select_view({ord('1'): "signal", ord('2'): "noise", ord('3'): "activity"}[key])
                    continue
                if self.job is not None and key not in (ord('l'), ord('L'), curses.KEY_DOWN, ord('j'),
                        curses.KEY_UP, ord('k'), curses.KEY_NPAGE, curses.KEY_PPAGE, curses.KEY_MOUSE):
                    continue
                if key in (ord('q'), ord('Q')):
                    return
                if key in (ord('b'), ord('B')) and hasattr(self.tab, "poll_reminders"):
                    self.select_view("signal" if self.show_backlog else "backlog")
                    continue
                if key in (ord('i'), ord('I')) and hasattr(self.tab, "get_filters"):
                    screen.timeout(-1)
                    try:
                        self.edit_filters(screen)
                    finally:
                        screen.timeout(100)
                    continue
                if key in (ord('l'), ord('L')):
                    self.select_view("signal" if self.show_activity else "activity")
                    continue
                if key in (9, curses.KEY_RIGHT, curses.KEY_LEFT, curses.KEY_BTAB):
                    direction = -1 if key in (curses.KEY_LEFT, curses.KEY_BTAB) else 1
                    self.tab_index = (self.tab_index + direction) % len(self.tabs)
                    self.select_view("signal")
                elif key in (curses.KEY_DOWN, ord('j'), curses.KEY_UP, ord('k')):
                    self.selected = max(0, min(len(self.rows()) - 1, self.selected +
                                             (1 if key in (curses.KEY_DOWN, ord('j')) else -1)))
                    self.scroll = 0
                    current_rows = self.rows()
                    self.rendered_selection = current_rows[self.selected][0] if current_rows else None
                elif key in (curses.KEY_NPAGE, curses.KEY_PPAGE):
                    self.scroll = max(0, self.scroll + (5 if key == curses.KEY_NPAGE else -5))
                elif key == curses.KEY_MOUSE:
                    _, x, y, _, _ = curses.getmouse()
                    for row, left, right, (kind, value) in self.clicks:
                        if row == y and left <= x < right:
                            if kind == "action":
                                if self.job is None:
                                    self.action(value, screen)
                            elif kind == "view":
                                self.select_view(value)
                            elif kind == "tab":
                                if self.job is None:
                                    self.tab_index, self.selected, self.scroll = value, 0, 0
                                    self.select_view("signal")
                            else:
                                self.selected, self.scroll = value, 0
                                self.rendered_selection = self.rows()[value][0]
                            break
                elif 0 <= key < 256 and chr(key).lower() in ("c", "t", "s", "a", "x", "e", "f", "r", "h", "d"):
                    self.action(chr(key).lower(), screen)
            except (ValueError, KeyError, OSError, RuntimeError) as error:
                self.notice = f"Error: {error}"


def main():
    parser = argparse.ArgumentParser(description="Agent Swarm chat, mail, and calendar terminal interface")
    parser.add_argument("--state-dir", type=Path, default=Path(".agent-state"))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--live", action="store_true", help="Real Gmail mode (default)")
    mode.add_argument("--demo", action="store_true", help="Explicitly use fixture emails and scripted models")
    args = parser.parse_args()
    try:
        app = TerminalApp(agent_tabs(args.state_dir, live=not args.demo))
        app.tab_index = next((i for i, tab in enumerate(app.tabs) if getattr(tab, "is_chat", False)), 0)
        try:
            curses.wrapper(app.run)
        finally:
            app.executor.shutdown(wait=True)
    except KeyboardInterrupt:
        pass
    except (ValueError, RuntimeError, ImportError, OSError) as error:
        parser.exit(1, f"{error}\nSee docs/gmail-setup.md for setup.\n")


if __name__ == "__main__":
    main()
