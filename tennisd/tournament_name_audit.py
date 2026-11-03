"""Candidate address collisions for human review; never infer tournament aliases."""
from collections import defaultdict

from sqlalchemy import func, select

from . import db
from .identities import catalog_is_visible
from .models import Match
from .tournament_catalog import tournament_slug
from .tournament_urls import explicit_tournament_slug


def tournament_name_report(limit=50):
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
        raise ValueError('Limit must be an integer between 1 and 500.')
    rows = db.session.execute(select(
        Match.tour, Match.tournament, func.count(Match.id),
        func.min(Match.week_start), func.max(Match.week_start),
    ).where(catalog_is_visible()).group_by(Match.tour, Match.tournament)
      .order_by(Match.tour, Match.tournament)).all()
    groups = defaultdict(list)
    for tour, name, count, first, last in rows:
        groups[(tour, tournament_slug(name))].append({
            'name': name, 'matches': count, 'first_date': first.isoformat(),
            'last_date': last.isoformat(), 'explicit_slug': explicit_tournament_slug(name),
        })
    collisions = [(key, variants) for key, variants in sorted(groups.items()) if len(variants) > 1]
    return {
        'schema_version': 1, 'scope': 'visible_catalog_names', 'name_count': len(rows),
        'collision_group_count': len(collisions), 'returned_groups': min(limit, len(collisions)),
        'has_more': len(collisions) > limit,
        'interpretation': 'Address collisions only; not proof of shared tournament identity. No merge proposed.',
        'groups': [{'tour': tour, 'slug': slug, 'variants': variants}
                   for (tour, slug), variants in collisions[:limit]],
    }
