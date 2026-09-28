"""Bounded model/tool loop. No approvals, credentials, or provider writes here."""
import json
from .tools import SYSTEM, TOOLS


class ChatLoop:
    def __init__(self, complete, dispatch, progress=lambda message: None, max_steps=6):
        self.complete, self.dispatch, self.progress, self.max_steps = complete, dispatch, progress, max_steps

    def run(self, messages, context):
        # Bounded history: no whole-inbox prompts or hidden recursive agent calls.
        history = [{'role': m['role'], 'content': m['content'][:7000]} for m in messages[-12:]]
        observations = []
        for step in range(self.max_steps):
            self.progress(f'Chat: interpreting request (step {step + 1}/{self.max_steps})…')
            raw = self.complete(SYSTEM, json.dumps({'tools': TOOLS, 'context': context() if callable(context) else context,
                                                  'conversation': history, 'tool_results': observations}))
            if len(raw) > 30000:
                raise ValueError('Chat model response exceeded its size limit')
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError('Chat model returned an invalid response')
            if value.get('type') == 'reply':
                text = value.get('text')
                if not isinstance(text, str) or not text.strip() or len(text) > 16000:
                    raise ValueError('Chat model returned an invalid answer')
                return text
            name, arguments = value.get('name'), value.get('arguments')
            if value.get('type') != 'tool' or name not in TOOLS or not isinstance(arguments, dict):
                raise ValueError('Chat model requested an unsupported tool; no action taken')
            self.progress(f'Chat tool: {name}')
            try:
                result = self.dispatch(name, arguments)
            except (ValueError, KeyError, RuntimeError, OSError, TypeError) as error:
                result = {'error': str(error), 'tool': name}
            encoded = json.dumps(result, ensure_ascii=False)
            observations.append({'tool': name, 'arguments': arguments,
                                 'result': encoded[:16000], 'truncated': len(encoded) > 16000})
        return 'I reached the tool-step limit for this turn. The completed work and proposals are shown below; ask me to continue if needed.'
