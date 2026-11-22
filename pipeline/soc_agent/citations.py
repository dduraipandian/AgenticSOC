"""Is an evidence value really in the tool result it cites? One check for the agent loop (which marks each
submitted fact verified or not) and the grounding grader (which scores it), so the portal and the eval never
disagree about the same fact."""

import re
from typing import Any

_TOKEN_SPLIT = re.compile(r"[\s,;()\[\]{}<>'\"`=|]+")
_EDGE = ".:-"  # sentence punctuation around a token, not part of it


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def leaves(value: Any, keys: bool = False) -> list[str]:
    """The string and number values inside a value, at any depth. Booleans and nulls carry no fact to check.
    keys=True adds the dict keys too: for what the agent was shown, where a value copied with its key
    ("first_seen: 2026-11-19") is a faithful copy. Never for evidence, whose own keys are not facts."""
    if isinstance(value, dict):
        return [leaf for k, v in value.items() for leaf in ([str(k)] if keys else []) + leaves(v, keys)]
    if isinstance(value, list | tuple):
        return [leaf for v in value for leaf in leaves(v, keys)]
    if isinstance(value, bool) or value is None:
        return []
    return [str(value)]  # numbers are compared as text


def facts(value: str) -> list[str]:
    """The concrete facts in a value: tokens with a digit or one of / . _ - @ (pids, paths, hosts, users like
    arun.m, times, techniques). Plain words are not facts."""
    tokens = (t.strip(_EDGE) for t in _TOKEN_SPLIT.split(normalise(value)))
    return [t for t in tokens if t and re.search(r"[\d/._@-]", t)]


def missing_facts(value: str, haystack: str) -> list[str]:
    """What of `value` is not in `haystack` (already normalised): its concrete facts that are absent, or, for a
    value with none, the value itself when it does not appear as written. Empty when the value is supported."""
    found = facts(value)
    if found:
        return [f for f in found if f not in haystack]
    return [] if normalise(value) in haystack else [normalise(value)]


def missing_from(value: str, result: Any) -> list[str]:
    """What of `value` is not in this tool result (or case text): empty when the value is backed by it."""
    return missing_facts(value, normalise("\n".join(leaves(result, keys=True))))


def is_cited(value: str, result: Any) -> bool:
    """Is `value` backed by this tool result? "pid 472 'cat /etc/shadow'" is when 472 and /etc/shadow are in it,
    however the words around them are put."""
    return not missing_from(value, result)
