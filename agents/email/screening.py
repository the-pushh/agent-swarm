"""Newsletter intent gate: deterministic sender rules, typed topic decisions."""
from dataclasses import asdict, dataclass, field
from email.utils import parseaddr
import hashlib
import json


@dataclass
class NewsletterFilters:
    include_topics: list[str] = field(default_factory=list)
    exclude_topics: list[str] = field(default_factory=list)
    include_senders: list[str] = field(default_factory=list)
    exclude_senders: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or set(value) - set(cls.__dataclass_fields__):
            raise ValueError("Invalid newsletter filters")
        if any(not isinstance(v, list) or any(not isinstance(s, str) or not s.strip() for s in v)
               for v in value.values()):
            raise ValueError("Each filter must be a list of nonempty strings")
        result = cls(**value)
        for rule in result.include_senders + result.exclude_senders:
            if not (rule.startswith('@') and len(rule) > 1 or '@' in rule and ' ' not in rule):
                raise ValueError("Sender rules must be email addresses or @domain.com")
        return result

    @property
    def revision(self):
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()

    @property
    def configured(self):
        return any(asdict(self).values())


def sender_matches(sender, rules):
    address = parseaddr(sender)[1].casefold()
    return any(address == rule.casefold() or
               (rule.startswith('@') and address.endswith(rule.casefold())) for rule in rules)


def screen_newsletter(message, filters, classifier):
    """A hard gate before GLM summarization; exclusions override inclusions."""
    if not filters.configured:
        return {"decision": "held", "reason": "Set newsletter topics or senders with I Filters."}
    sender = message.actual_sender or message.sender
    if sender_matches(sender, filters.exclude_senders):
        return {"decision": "exclude", "reason": "Sender matched an exclusion."}
    included = sender_matches(sender, filters.include_senders)
    topic = None
    if filters.exclude_topics or (filters.include_topics and not included):
        if classifier is None:
            return {"decision": "held", "reason": "Topic classifier unavailable."}
        topic = classifier.match_topics(message, filters.include_topics, filters.exclude_topics)
        if topic == "exclude":
            return {"decision": "exclude", "reason": "Content matched an excluded topic."}
        if topic == "uncertain":
            return {"decision": "held", "reason": "Topic relevance uncertain; inspect Activity."}
    if included or topic == "include" or not (filters.include_senders or filters.include_topics):
        return {"decision": "include", "reason": "Matches inclusion rules or passes exclusions-only rules."}
    return {"decision": "exclude", "reason": "No included topic or sender matched."}
