"""Unambiguous archive addresses without merging stored event identities."""
import hashlib

from werkzeug.exceptions import HTTPException

from .tournament_catalog import tournament_slug


def tournament_key(name):
    return hashlib.sha256(name.encode('utf-8')).hexdigest()


def explicit_tournament_slug(name):
    return tournament_slug(name) + '--' + tournament_key(name)


class TournamentAmbiguity(HTTPException):
    code = 300

    def __init__(self, names):
        super().__init__()
        self.names = names


def resolve_name(names, slug):
    base, separator, key = slug.partition('--')
    candidates = [name for name in names if tournament_slug(name) == base]
    if separator:
        candidates = [name for name in candidates if tournament_key(name) == key]
    if len(candidates) > 1:
        raise TournamentAmbiguity(candidates)
    return candidates[0] if candidates else None


def sitemap_event_slugs(pairs):
    groups = {}
    for tour, name in pairs:
        groups.setdefault((tour, tournament_slug(name)), set()).add(name)
    return {(tour, name): explicit_tournament_slug(name) if len(names) > 1 else slug
            for (tour, slug), names in groups.items() for name in names}
