"""Deterministic sender counts over observed mail, never claimed as mailbox totals."""

from datetime import datetime, timedelta, timezone
from email.utils import parseaddr


def sender_key(sender):
    return (parseaddr(sender)[1] or sender).strip().casefold()


def frequency_for(sender, observations, now=None):
    now = now or datetime.now(timezone.utc)
    matching = [item for item in observations.values() if sender_key(item["sender"]) == sender_key(sender)]
    dates = []
    for item in matching:
        try:
            received = datetime.fromisoformat(item["received_at"])
            if received.tzinfo is not None:
                dates.append(received)
        except (ValueError, TypeError, KeyError):
            pass
    return {
        "observed_messages": len(matching),
        "observed_last_7_days": sum(now - timedelta(days=7) <= date <= now for date in dates),
        "observed_last_30_days": sum(now - timedelta(days=30) <= date <= now for date in dates),
        "unknown_date_count": len(matching) - len(dates),
        "basis": "Distinct scanned inbox messages including this page; partial history, not mailbox totals",
    }
