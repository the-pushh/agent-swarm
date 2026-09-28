"""Describe recorded research activity without additional model requests."""
import re


def research_activity(record, task_title):
    lines = []
    sources = record.get('sources', {})
    for event in record.get('events', [])[-80:]:
        message = str(event.get('message', ''))
        if event.get('query'):
            message = f"Searching: {event['query']}"
        elif event.get('source_id') and event.get('url'):
            source = sources.get(event['source_id'], {})
            title = source.get('title') or event['source_id']
            message = f"Reading: {title} — {event['url']}"
        elif re.fullmatch(r'(Research|Owner): model call \d+', message):
            role = message.split(':', 1)[0]
            message = f'{role} working on: {task_title}'
        if message and (not lines or message != lines[-1]):
            lines.append(message)
    return lines
