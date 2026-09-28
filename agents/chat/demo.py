"""Small scripted chat demo. Live mode uses GLM through OpenRouter."""
import json


def complete(system, user):
    data = json.loads(user)
    if data.get('task') == 'reply_draft':
        return json.dumps({'body': "Thanks, Alex — I've received your message with the meeting notes."})
    if 'conversation' not in data:
        return json.dumps({'body': 'Could we find another time for this meeting?'})
    latest = next(m['content'] for m in reversed(data['conversation']) if m['role'] == 'user').lower()
    results = data['tool_results']
    def tool(name, arguments=None):
        return json.dumps({'type': 'tool', 'name': name, 'arguments': arguments or {}})
    def reply(text):
        return json.dumps({'type': 'reply', 'text': text})
    if results:
        value = json.loads(results[-1]['result'])
        if 'error' in value:
            return reply(value['error'])
        if results[-1]['tool'] == 'email_scan' and 'attention' in latest:
            return tool('calendar_events')
        if results[-1]['tool'] == 'email_scan':
            rows = value['signal']['items']
            return reply('Demo inbox: ' + '\n'.join(f"{r['summary']} ({r['ref']})" for r in rows))
        if results[-1]['tool'] == 'calendar_events':
            mail = next((json.loads(r['result']) for r in results if r['tool'] == 'email_scan'), None)
            intro = ('Demo inbox:\n' + '\n'.join(r['summary'] for r in mail['signal']['items']) + '\n\n') if mail else ''
            return reply(intro + 'Demo commitments:\n' + '\n'.join(f"{e['title']} — {e['start']}" for e in value['items']))
        return reply('Prepared locally. Review the exact options below; nothing has been sent or booked.')
    if 'draft' in latest and ('reply' in latest or 'alex' in latest):
        return tool('email_draft', {'id': 'm1', 'instructions': latest})
    if any(word in latest for word in ('inbox', 'email', 'mail', 'urgent', 'attention')):
        return tool('email_scan')
    if any(word in latest for word in ('block', 'focus', 'find a time', 'schedule')):
        return tool('calendar_plan', {'kind': 'block' if 'block' in latest or 'focus' in latest else 'find',
                                     'title': 'Focus time' if 'focus' in latest else 'Meeting', 'duration': 60, 'days': 7})
    if any(word in latest for word in ('calendar', 'commitment', 'tomorrow', 'week')):
        return tool('calendar_events')
    return reply('Offline demo: try “What emails need my reply?”, “Show my commitments”, or “Block an hour for focus work”. Live chat uses GLM for general requests.')
