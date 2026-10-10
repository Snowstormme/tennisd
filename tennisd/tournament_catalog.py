"""Presentation metadata for tournament pages.

Results and statistics always come from the local match catalog. This module only
adds editorial context and openly licensed trophy artwork.
"""

import re
import unicodedata
from urllib.parse import quote


def tournament_slug(name):
    value = unicodedata.normalize("NFKD", name or "")
    value = "".join(character for character in value if not unicodedata.combining(character))
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")


def commons_image(filename, width=1600):
    return f"https://commons.wikimedia.org/wiki/Special:Redirect/file/{quote(filename, safe='')}?width={width}"


GENERIC_TROPHY = {
    "image_url": commons_image("Betty Ford's tennis trophy.JPG"),
    "image_page": "https://commons.wikimedia.org/wiki/File:Betty_Ford%27s_tennis_trophy.JPG",
    "image_credit": "Gerald R. Ford Presidential Museum · public domain",
}


TOURNAMENT_PROFILES = {
    "australian open": {
        "location": "Melbourne, Australia",
        "founded": 1905,
        "summary": "The opening Grand Slam of the season, played at Melbourne Park on hard courts.",
        "official_url": "https://ausopen.com/",
        "image_url": commons_image("Stan Wawrinka Norman Brookes Challenge Cup.jpg"),
        "image_page": "https://commons.wikimedia.org/wiki/File:Stan_Wawrinka_Norman_Brookes_Challenge_Cup.jpg",
        "image_credit": "Tourism Victoria · CC BY 2.0",
    },
    "roland garros": {
        "location": "Paris, France",
        "founded": 1891,
        "summary": "The Paris Grand Slam and the defining clay-court championship of the season.",
        "official_url": "https://www.rolandgarros.com/",
        "image_url": commons_image("Coupe des Mousquetaires (cropped).jpg"),
        "image_page": "https://commons.wikimedia.org/wiki/File:Coupe_des_Mousquetaires_(cropped).jpg",
        "image_credit": "Chabe01 / Kacir · CC BY-SA 4.0",
    },
    "french open": {
        "location": "Paris, France",
        "founded": 1891,
        "summary": "The Paris Grand Slam and the defining clay-court championship of the season.",
        "official_url": "https://www.rolandgarros.com/",
        "image_url": commons_image("Coupe des Mousquetaires (cropped).jpg"),
        "image_page": "https://commons.wikimedia.org/wiki/File:Coupe_des_Mousquetaires_(cropped).jpg",
        "image_credit": "Chabe01 / Kacir · CC BY-SA 4.0",
    },
    "wimbledon": {
        "location": "London, United Kingdom",
        "founded": 1877,
        "summary": "The oldest Grand Slam and the only major still contested on grass.",
        "official_url": "https://www.wimbledon.com/",
        "image_url": commons_image("Gentlemen's Singles Trophy Wimbledon 2023.jpg"),
        "image_page": "https://commons.wikimedia.org/wiki/File:Gentlemen%27s_Singles_Trophy_Wimbledon_2023.jpg",
        "image_credit": "Daniel Cooper · CC BY-SA 2.0",
    },
    "us open": {
        "location": "New York, United States",
        "founded": 1881,
        "summary": "The final Grand Slam of the season, played on hard courts in Flushing Meadows.",
        "official_url": "https://www.usopen.org/",
        "image_url": commons_image("P20250907DT-0489 President Donald Trump attends the U.S. Open Men’s Championship.jpg"),
        "image_page": "https://commons.wikimedia.org/wiki/File:P20250907DT-0489_President_Donald_Trump_attends_the_U.S._Open_Men%E2%80%99s_Championship.jpg",
        "image_credit": "Daniel Torok / The White House · public domain",
    },
}


# Provider tournament names often include tour prefixes, category suffixes or
# a repeated host city. Slug aliases keep presentation metadata independent of
# those spelling differences.
TOURNAMENT_LOCATIONS = {
    "antofagasta": "Antofagasta, Chile",
    "braga": "Braga, Portugal",
    "china-open": "Beijing, China",
    "beijing": "Beijing, China",
    "samsun": "Samsun, Türkiye",
    "paris-masters": "Paris, France",
    "indian-wells": "Indian Wells, United States",
    "miami-open": "Miami, United States",
    "monte-carlo": "Monte Carlo, Monaco",
    "madrid-open": "Madrid, Spain",
    "italian-open": "Rome, Italy",
    "rome-masters": "Rome, Italy",
    "canadian-open": "Toronto / Montreal, Canada",
    "cincinnati-open": "Cincinnati, United States",
    "shanghai-masters": "Shanghai, China",
    "doha": "Doha, Qatar",
    "dubai": "Dubai, United Arab Emirates",
}


LEVEL_DETAILS = {
    "G": (0, "Grand Slam", "Major"),
    "F": (1, "Tour Finals", "Season finale"),
    "M": (2, "Masters 1000", "1000 level"),
    "PM": (2, "WTA 1000", "1000 level"),
    "P": (3, "WTA Premier", "Premier level"),
    "A": (4, "ATP Tour", "Tour level"),
    "I": (4, "WTA International", "Tour level"),
    "O": (1, "Olympic Games", "Global event"),
    "D": (3, "Team event", "Team competition"),
}


def tournament_level(levels):
    details = [LEVEL_DETAILS[level] for level in levels if level in LEVEL_DETAILS]
    return min(details, default=(9, "Tour event", "Tour event"), key=lambda item: item[0])


def tournament_profile(name):
    normalized_name = tournament_slug(name)
    profile = dict(GENERIC_TROPHY)
    known_profile = TOURNAMENT_PROFILES.get((name or "").casefold())
    if known_profile is None:
        for known_name, candidate in TOURNAMENT_PROFILES.items():
            known_slug = tournament_slug(known_name)
            if known_slug and known_slug in normalized_name:
                known_profile = candidate
                break
    profile.update(known_profile or {})
    if "location" not in profile:
        for alias, location in TOURNAMENT_LOCATIONS.items():
            if alias in normalized_name:
                profile["location"] = location
                break
    profile.setdefault("location", "International tour event")
    profile.setdefault("founded", None)
    profile.setdefault(
        "summary",
        "Explore this tournament through the results, champions and seasons preserved in the Tennisd archive.",
    )
    profile.setdefault("official_url", None)
    return profile
