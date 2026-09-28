"""Choose adapters, lock state, and invoke the calendar control loop."""
from contextlib import contextmanager
from pathlib import Path
import fcntl
import json
import os
from .control_loop import CalendarLoop, new_state


@contextmanager
def open_agent(state_path, calendar, model, progress=lambda message: None, on_checkpoint=lambda state: None, now=None):
    state_path = Path(state_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with state_path.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = json.loads(state_path.read_text()) if state_path.exists() else new_state()
        if state.get('identity', calendar.state_identity) != calendar.state_identity:
            raise ValueError('Calendar state belongs to another account/calendar')
        state['identity'] = calendar.state_identity
        def checkpoint():
            temporary = state_path.with_suffix('.tmp')
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, 'w') as file:
                json.dump(state, file, indent=2)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, state_path)
            on_checkpoint(state)
        for item in state['proposals'].values():
            if item['status'] == 'executing':
                item['status'] = 'uncertain'
                checkpoint()
        yield CalendarLoop(calendar, model, state, checkpoint, progress, now)
