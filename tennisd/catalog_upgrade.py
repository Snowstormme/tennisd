"""Idempotently normalize the legacy match catalog into the expanded schema."""

from sqlalchemy import select

from . import db
from .models import Match, MatchParticipant, MatchStatistic, PlayerExternalId, RankingSnapshot, Tournament, TournamentEdition
from .tournament_catalog import tournament_profile, tournament_slug


def upgrade_catalog():
    # Load existing keys once. The production archive contains tens of thousands
    # of matches, so querying every target table for every row would turn this
    # into a very slow network-bound migration against Neon.
    tournaments = {(item.tour, item.slug): item for item in db.session.scalars(select(Tournament)).all()}
    editions = {
        (item.tournament_id, item.season): item
        for item in db.session.scalars(select(TournamentEdition)).all()
    }
    participant_keys = set(db.session.execute(select(MatchParticipant.match_id, MatchParticipant.side)).all())
    statistic_keys = set(db.session.execute(select(MatchStatistic.match_id, MatchStatistic.player_id)).all())
    ranking_keys = set(
        db.session.execute(
            select(RankingSnapshot.player_id, RankingSnapshot.ranked_on, RankingSnapshot.ranking_type)
        ).all()
    )
    external_ids = set(
        db.session.execute(select(PlayerExternalId.provider, PlayerExternalId.external_id)).all()
    )
    matches = db.session.scalars(select(Match).order_by(Match.week_start, Match.id)).all()
    updated = 0
    for match in matches:
        key = (match.tour, tournament_slug(match.tournament))
        tournament = tournaments.get(key)
        if tournament is None:
            profile = tournament_profile(match.tournament)
            tournament = Tournament(tour=match.tour, slug=key[1], name=match.tournament, location=profile.get("location"))
            db.session.add(tournament)
            db.session.flush()
        tournaments[key] = tournament
        edition_key = (tournament.id, match.week_start.year)
        edition = editions.get(edition_key)
        if edition is None:
            edition = TournamentEdition(tournament_id=tournament.id, season=match.week_start.year, level=match.level, surface=match.surface, starts_on=match.week_start, status="finished")
            db.session.add(edition)
            db.session.flush()
        editions[edition_key] = edition
        if match.edition_id != edition.id:
            match.edition_id, match.status = edition.id, match.status or "finished"
            updated += 1
        sides = (
            (1, match.winner_id, True, match.winner_rank, (match.w_ace, match.w_df, match.w_svpt, match.w_first_in, match.w_first_won, match.w_bp_saved, match.w_bp_faced)),
            (2, match.loser_id, False, match.loser_rank, (match.l_ace, match.l_df, match.l_svpt, match.l_first_in, match.l_first_won, match.l_bp_saved, match.l_bp_faced)),
        )
        for side, player_id, won, rank, stats in sides:
            participant_key = (match.id, side)
            if participant_key not in participant_keys:
                db.session.add(MatchParticipant(match_id=match.id, player_id=player_id, side=side, is_winner=won))
                participant_keys.add(participant_key)
            statistic_key = (match.id, player_id)
            if any(value is not None for value in stats) and statistic_key not in statistic_keys:
                db.session.add(MatchStatistic(match_id=match.id, player_id=player_id, aces=stats[0], double_faults=stats[1], serve_points=stats[2], first_serves_in=stats[3], first_serve_points_won=stats[4], break_points_saved=stats[5], break_points_faced=stats[6], source="sackmann"))
                statistic_keys.add(statistic_key)
            ranking_key = (player_id, match.week_start, "singles")
            if rank and ranking_key not in ranking_keys:
                db.session.add(RankingSnapshot(player_id=player_id, ranked_on=match.week_start, ranking_type="singles", rank=rank, source="sackmann-match"))
                ranking_keys.add(ranking_key)
            external_id = player_id.split("-", 1)[-1]
            external_key = ("sackmann", external_id)
            if external_key not in external_ids:
                db.session.add(PlayerExternalId(player_id=player_id, provider="sackmann", external_id=external_id))
                external_ids.add(external_key)
        if updated and updated % 1000 == 0:
            db.session.commit()
    db.session.commit()
    return {"matches": len(matches), "updated": updated, "tournaments": len(tournaments), "editions": len(editions)}
