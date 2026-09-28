"""Title-only topic discovery. Humans, never the model, assign relevance."""
from dataclasses import asdict
from datetime import datetime, timezone
from email.utils import parseaddr
from time import monotonic
from .screening import NewsletterFilters
from .tools import MessageTitle


def filters_ready(state):
    return bool(state.get('newsletter_preferences_confirmed') or
                NewsletterFilters.from_dict(state.get('newsletter_filters', {})).configured)


def load_titles(loop, identifiers):
    """Reuse immutable message metadata across Analyze, Scan, and interrupted scans."""
    cache = loop.state.setdefault('title_cache', {})
    missing = [identifier for identifier in identifiers if identifier not in cache]
    if missing:
        titles = loop.gmail.read_titles(missing)
        if len(titles) != len(missing) or [t.id for t in titles] != missing:
            raise ValueError('Provider returned different or missing titles')
        cache.update({title.id: asdict(title) for title in titles})
        loop.checkpoint()
    return [MessageTitle(**cache[identifier]) for identifier in identifiers]


def analyze_preferences(loop, limit=50):
    if not 1 <= limit <= 50:
        raise ValueError('Title sample limit must be between 1 and 50')
    started = monotonic()
    loop.progress(f'Analyze: fetching up to {limit} subjects and senders in a metadata batch; no bodies…')
    page = loop.gmail.list_inbox(None, limit)
    if len(page.message_ids) > limit:
        raise ValueError('Provider exceeded title sample limit')
    titles = load_titles(loop, page.message_ids)
    metadata_seconds = monotonic() - started
    sample = [asdict(t) for t in titles]
    previous = loop.state.get('title_analysis', {})
    if sample == previous.get('sample'):
        loop.progress('Titles unchanged; reusing saved topics without a model call.')
        return
    loop.progress(f'Analyze: {len(titles)} titles ready in {metadata_seconds:.1f}s; extracting topics with one GLM call…')
    started = monotonic()
    topics = loop.model.analyze_titles(titles) if titles else []
    topic_seconds = monotonic() - started
    candidates = [{'kind': 'topic', 'value': topic['name'], 'evidence_ids': topic['evidence_ids']} for topic in topics]
    senders = {}
    for title in titles:
        sender = parseaddr(title.sender)[1].casefold()
        if sender and '@' in sender:
            senders.setdefault(sender, []).append(title.id)
    candidates += [{'kind': 'sender', 'value': sender, 'evidence_ids': ids}
                   for sender, ids in sorted(senders.items(), key=lambda entry: (-len(entry[1]), entry[0]))]
    loop.state['title_analysis'] = {
        'sample': sample, 'candidates': candidates, 'more_pages': bool(page.next_cursor),
        'query': getattr(loop.gmail, 'query', None), 'created_at': datetime.now(timezone.utc).isoformat(),
        'timings': {'gmail_metadata': round(metadata_seconds, 3), 'glm_topics': round(topic_seconds, 3)},
    }
    loop.checkpoint()
    loop.progress(f'Analyze complete: {len(topics)} topics, {len(senders)} senders. GLM: {topic_seconds:.1f}s. Mark Include or Exclude, then save.')


def candidate_key(candidate):
    return f"{candidate['kind']}:{candidate['value'].casefold()}"


def choices_for(analysis, filters):
    choices = {}
    for candidate in analysis.get('candidates', []):
        field = 'topics' if candidate['kind'] == 'topic' else 'senders'
        value = candidate['value'].casefold()
        # Existing explicit rules appear selected; newly discovered rows start undecided.
        for decision in ('include', 'exclude'):
            if value in {v.casefold() for v in filters.get(f'{decision}_{field}', [])}:
                choices[candidate_key(candidate)] = decision
    return choices


def filters_from_choices(analysis, choices, existing):
    result = asdict(NewsletterFilters.from_dict(existing))
    for candidate in analysis.get('candidates', []):
        field = 'topics' if candidate['kind'] == 'topic' else 'senders'
        for decision in ('include', 'exclude'):
            name = f'{decision}_{field}'
            result[name] = [v for v in result[name] if v.casefold() != candidate['value'].casefold()]
        decision = choices.get(candidate_key(candidate))
        if decision is not None:
            if decision not in ('include', 'exclude'):
                raise ValueError('Choose Include, Exclude, or leave undecided')
            result[f'{decision}_{field}'].append(candidate['value'])
    return result
