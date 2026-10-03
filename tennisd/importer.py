"""Small local fixtures used only for development and automated tests."""

from datetime import datetime

from sqlalchemy import select

from . import db
from .models import Match, Player


DEMO_PLAYERS = {
    "ATP": {
        "100644": {"ioc": "GER", "hand": "R", "height": "198", "dob": "19970420", "wikidata_id": "Q13990552"},
        "104925": {"ioc": "SRB", "hand": "R", "height": "188", "dob": "19870522", "wikidata_id": "Q5812"},
        "106421": {"ioc": "RUS", "hand": "R", "height": "198", "dob": "19960211", "wikidata_id": "Q21622022"},
        "126203": {"ioc": "USA", "hand": "R", "height": "196", "dob": "19971028", "wikidata_id": "Q17660516"},
        "206173": {"ioc": "ITA", "hand": "R", "height": "191", "dob": "20010816", "wikidata_id": "Q54812588"},
        "207989": {"ioc": "ESP", "hand": "R", "height": "183", "dob": "20030505", "wikidata_id": "Q85518537"},
    },
    "WTA": {
        "201619": {"ioc": "USA", "hand": "R", "height": "178", "dob": "19950217", "wikidata_id": "Q34403"},
        "202468": {"ioc": "USA", "hand": "R", "height": "170", "dob": "19940224", "wikidata_id": "Q24159"},
        "206252": {"ioc": "CZE", "hand": "R", "height": "178", "dob": "19951218", "wikidata_id": "Q23959634"},
        "214981": {"ioc": "USA", "hand": "R", "height": "175", "dob": "20040313", "wikidata_id": "Q66793148"},
        "216347": {"ioc": "POL", "hand": "R", "height": "176", "dob": "20010531", "wikidata_id": "Q56488014"},
        "221815": {"ioc": "CHN", "hand": "R", "height": "178", "dob": "20021008", "wikidata_id": "Q97647457"},
        "223341": {"ioc": "USA", "hand": "R", "height": "180", "dob": "20010831", "wikidata_id": "Q28658562"},
        "225805": {"ioc": "BLR", "hand": "R", "height": "182", "dob": "19980505", "wikidata_id": "Q24059291"},
    },
}


DEMO_MATCHES = [
    ("ATP", "2024-580", "Australian Open", "Hard", "G", "20240115", "226", "206173", "Jannik Sinner", "106421", "Daniil Medvedev", "3-6 3-6 6-4 6-4 6-3", "F", "5", "224", "4", "3"),
    ("ATP", "2024-520", "Roland Garros", "Clay", "G", "20240527", "401", "207989", "Carlos Alcaraz", "100644", "Alexander Zverev", "6-3 2-6 5-7 6-1 6-2", "F", "5", "", "3", "4"),
    ("ATP", "2024-540", "Wimbledon", "Grass", "G", "20240701", "226", "207989", "Carlos Alcaraz", "104925", "Novak Djokovic", "6-2 6-2 7-6(4)", "F", "5", "147", "3", "2"),
    ("ATP", "2024-560", "US Open", "Hard", "G", "20240826", "226", "206173", "Jannik Sinner", "126203", "Taylor Fritz", "6-3 6-4 7-5", "F", "5", "136", "1", "12"),
    ("ATP", "2025-580", "Australian Open", "Hard", "G", "20250113", "226", "206173", "Jannik Sinner", "100644", "Alexander Zverev", "6-3 7-6(4) 6-3", "F", "5", "162", "1", "2"),
    ("ATP", "2025-520", "Roland Garros", "Clay", "G", "20250526", "401", "207989", "Carlos Alcaraz", "206173", "Jannik Sinner", "4-6 6-7(4) 6-4 7-6(3) 7-6(2)", "F", "5", "", "2", "1"),
    ("ATP", "2025-540", "Wimbledon", "Grass", "G", "20250630", "226", "206173", "Jannik Sinner", "207989", "Carlos Alcaraz", "4-6 6-4 6-4 6-4", "F", "5", "", "1", "2"),
    ("ATP", "2025-560", "US Open", "Hard", "G", "20250825", "226", "207989", "Carlos Alcaraz", "206173", "Jannik Sinner", "6-2 3-6 6-1 6-4", "F", "5", "", "2", "1"),
    ("WTA", "2024-580", "Australian Open", "Hard", "G", "20240115", "226", "225805", "Aryna Sabalenka", "221815", "Qinwen Zheng", "6-3 6-2", "F", "3", "76", "2", "15"),
    ("WTA", "2024-520", "Roland Garros", "Clay", "G", "20240527", "226", "216347", "Iga Swiatek", "230234", "Jasmine Paolini", "6-2 6-1", "F", "3", "68", "1", "12"),
    ("WTA", "2024-540", "Wimbledon", "Grass", "G", "20240701", "226", "206252", "Barbora Krejcikova", "230234", "Jasmine Paolini", "6-2 2-6 6-4", "F", "3", "116", "32", "7"),
    ("WTA", "2024-560", "US Open", "Hard", "G", "20240826", "226", "225805", "Aryna Sabalenka", "202468", "Jessica Pegula", "7-5 7-5", "F", "3", "113", "2", "6"),
    ("WTA", "2025-580", "Australian Open", "Hard", "G", "20250113", "226", "201619", "Madison Keys", "225805", "Aryna Sabalenka", "6-3 2-6 7-5", "F", "3", "122", "14", "1"),
    ("WTA", "2025-520", "Roland Garros", "Clay", "G", "20250526", "226", "214981", "Coco Gauff", "225805", "Aryna Sabalenka", "6-7(5) 6-2 6-4", "F", "3", "", "2", "1"),
    ("WTA", "2025-540", "Wimbledon", "Grass", "G", "20250630", "226", "216347", "Iga Swiatek", "223341", "Amanda Anisimova", "6-0 6-0", "F", "3", "", "4", "13"),
    ("WTA", "2025-560", "US Open", "Hard", "G", "20250825", "226", "225805", "Aryna Sabalenka", "223341", "Amanda Anisimova", "6-3 7-6(3)", "F", "3", "", "1", "8"),
]


def number(value):
    try:
        return int(value) if value not in (None, "") else None
    except ValueError:
        return None


def date(value):
    try:
        return datetime.strptime(value, "%Y%m%d").date() if value else None
    except ValueError:
        return None


def player_from_row(tour, source_id, name, row, bio=None, cache=None):
    player_id = f"{tour.lower()}-{source_id}"
    player = cache.get(player_id) if cache is not None else None
    if player is None:
        player = db.session.get(Player, player_id)
    if player is None:
        player = Player(id=player_id, tour=tour, name=name)
        db.session.add(player)
    if cache is not None:
        cache[player_id] = player
    if name and not player.name:
        player.name = name
    player.country = player.country or row.get("ioc")
    player.hand = player.hand or row.get("hand")
    player.height_cm = player.height_cm or number(row.get("ht"))
    if bio:
        player.born_on = player.born_on or date(bio.get("dob"))
        player.wikidata_id = player.wikidata_id or bio.get("wikidata_id") or None
        player.height_cm = player.height_cm or number(bio.get("height"))
        player.hand = player.hand or bio.get("hand")
        player.country = player.country or bio.get("ioc")
    return player


def import_rows(tour, rows, bios=None):
    candidate_ids = [
        f"{tour.lower()}-{row.get('tourney_id')}-{row.get('match_num')}"
        for row in rows if row.get("tourney_id") and row.get("match_num")
    ]
    existing = set(
        db.session.scalars(select(Match.id).where(Match.id.in_(candidate_ids))).all()
    ) if candidate_ids else set()
    source_ids = {
        source_id
        for row in rows
        for source_id in (row.get("winner_id"), row.get("loser_id"))
        if source_id
    }
    player_ids = [f"{tour.lower()}-{source_id}" for source_id in source_ids]
    players = {
        player.id: player
        for player in db.session.scalars(select(Player).where(Player.id.in_(player_ids))).all()
    } if player_ids else {}
    added = 0
    bios = bios or {}
    for row in rows:
        winner_source_id = row.get("winner_id")
        loser_source_id = row.get("loser_id")
        week_start = date(row.get("tourney_date"))
        match_id = f"{tour.lower()}-{row.get('tourney_id')}-{row.get('match_num')}"
        if (
            not winner_source_id or not loser_source_id or not week_start
            or not row.get("match_num") or match_id in existing
            or row.get("score", "").strip().upper() in ("W/O", "WALKOVER", "DEF")
        ):
            continue

        winner = player_from_row(
            tour, winner_source_id, row.get("winner_name", ""),
            {"ioc": row.get("winner_ioc"), "hand": row.get("winner_hand"), "ht": row.get("winner_ht")},
            bios.get(winner_source_id),
            players,
        )
        loser = player_from_row(
            tour, loser_source_id, row.get("loser_name", ""),
            {"ioc": row.get("loser_ioc"), "hand": row.get("loser_hand"), "ht": row.get("loser_ht")},
            bios.get(loser_source_id),
            players,
        )
        match = Match(
            id=match_id,
            tour=tour,
            tournament=row.get("tourney_name", "Unknown"),
            level=row.get("tourney_level") or "?",
            surface=row.get("surface") or "Unknown",
            week_start=week_start,
            round=row.get("round") or "?",
            winner=winner,
            loser=loser,
            score=row.get("score") or "Score unavailable",
            best_of=number(row.get("best_of")),
            minutes=number(row.get("minutes")),
            winner_rank=number(row.get("winner_rank")),
            loser_rank=number(row.get("loser_rank")),
            w_ace=number(row.get("w_ace")),
            l_ace=number(row.get("l_ace")),
            w_df=number(row.get("w_df")),
            l_df=number(row.get("l_df")),
            w_svpt=number(row.get("w_svpt")),
            l_svpt=number(row.get("l_svpt")),
            w_first_in=number(row.get("w_1stIn")),
            l_first_in=number(row.get("l_1stIn")),
            w_first_won=number(row.get("w_1stWon")),
            l_first_won=number(row.get("l_1stWon")),
            w_bp_saved=number(row.get("w_bpSaved")),
            l_bp_saved=number(row.get("l_bpSaved")),
            w_bp_faced=number(row.get("w_bpFaced")),
            l_bp_faced=number(row.get("l_bpFaced")),
        )
        db.session.add(match)
        existing.add(match_id)
        added += 1
    db.session.commit()
    return added


def demo_row(item):
    (
        tour, event_id, event, surface, level, event_date, match_num,
        winner_id, winner_name, loser_id, loser_name, score, round_name,
        best_of, minutes, winner_rank, loser_rank,
    ) = item
    winner = DEMO_PLAYERS.get(tour, {}).get(winner_id, {})
    loser = DEMO_PLAYERS.get(tour, {}).get(loser_id, {})
    return {
        "tour": tour,
        "tourney_id": event_id,
        "tourney_name": event,
        "surface": surface,
        "tourney_level": level,
        "tourney_date": event_date,
        "match_num": match_num,
        "winner_id": winner_id,
        "winner_name": winner_name,
        "winner_ioc": winner.get("ioc"),
        "winner_hand": winner.get("hand"),
        "winner_ht": winner.get("height"),
        "loser_id": loser_id,
        "loser_name": loser_name,
        "loser_ioc": loser.get("ioc"),
        "loser_hand": loser.get("hand"),
        "loser_ht": loser.get("height"),
        "score": score,
        "best_of": best_of,
        "round": round_name,
        "minutes": minutes,
        "winner_rank": winner_rank,
        "loser_rank": loser_rank,
        "w_ace": "8",
        "l_ace": "5",
        "w_df": "3",
        "l_df": "4",
        "w_svpt": "96",
        "l_svpt": "92",
        "w_1stIn": "61",
        "l_1stIn": "58",
        "w_1stWon": "45",
        "l_1stWon": "38",
        "w_bpSaved": "5",
        "l_bpSaved": "4",
        "w_bpFaced": "7",
        "l_bpFaced": "8",
    }


def seed_samples():
    """Seed a compact handcrafted catalog for tests and local development."""
    if db.session.scalar(select(Match.id).limit(1)) is not None:
        return
    rows = [demo_row(item) for item in DEMO_MATCHES]
    for tour in ("ATP", "WTA"):
        import_rows(tour, [row for row in rows if row["tour"] == tour], DEMO_PLAYERS[tour])
