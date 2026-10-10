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
    "abu-dhabi": "Abu Dhabi, United Arab Emirates",
    "acapulco": "Acapulco, Mexico",
    "adelaide": "Adelaide, Australia",
    "almaty": "Almaty, Kazakhstan",
    "antofagasta": "Antofagasta, Chile",
    "antwerp": "Antwerp, Belgium",
    "astana": "Astana, Kazakhstan",
    "athens": "Athens, Greece",
    "atlanta": "Atlanta, United States",
    "auckland": "Auckland, New Zealand",
    "austin": "Austin, United States",
    "bad-homburg": "Bad Homburg, Germany",
    "banja-luka": "Banja Luka, Bosnia and Herzegovina",
    "barcelona": "Barcelona, Spain",
    "basel": "Basel, Switzerland",
    "bastad": "Bastad, Sweden",
    "belgrade": "Belgrade, Serbia",
    "berlin": "Berlin, Germany",
    "birmingham": "Birmingham, United Kingdom",
    "bogota": "Bogotá, Colombia",
    "braga": "Braga, Portugal",
    "brisbane": "Brisbane, Australia",
    "brussels": "Brussels, Belgium",
    "bucharest": "Bucharest, Romania",
    "budapest": "Budapest, Hungary",
    "buenos-aires": "Buenos Aires, Argentina",
    "cancun": "Cancún, Mexico",
    "charleston": "Charleston, United States",
    "chengdu": "Chengdu, China",
    "chennai": "Chennai, India",
    "china-open": "Beijing, China",
    "beijing": "Beijing, China",
    "cincinnati": "Cincinnati, United States",
    "cleveland": "Cleveland, United States",
    "cluj-napoca": "Cluj-Napoca, Romania",
    "cordoba": "Córdoba, Argentina",
    "dallas": "Dallas, United States",
    "delray-beach": "Delray Beach, United States",
    "eastbourne": "Eastbourne, United Kingdom",
    "estoril": "Estoril, Portugal",
    "geneva": "Geneva, Switzerland",
    "gstaad": "Gstaad, Switzerland",
    "guadalajara": "Guadalajara, Mexico",
    "guangzhou": "Guangzhou, China",
    "halle": "Halle, Germany",
    "hamburg": "Hamburg, Germany",
    "hangzhou": "Hangzhou, China",
    "hobart": "Hobart, Australia",
    "hong-kong": "Hong Kong",
    "houston": "Houston, United States",
    "hua-hin": "Hua Hin, Thailand",
    "iasi": "Iași, Romania",
    "jiujiang": "Jiujiang, China",
    "kitzbuhel": "Kitzbühel, Austria",
    "lausanne": "Lausanne, Switzerland",
    "linz": "Linz, Austria",
    "los-cabos": "Los Cabos, Mexico",
    "lyon": "Lyon, France",
    "madrid": "Madrid, Spain",
    "mallorca": "Mallorca, Spain",
    "marrakech": "Marrakech, Morocco",
    "marseille": "Marseille, France",
    "merida": "Mérida, Mexico",
    "metz": "Metz, France",
    "miami": "Miami, United States",
    "monastir": "Monastir, Tunisia",
    "monte-carlo-masters": "Monte Carlo, Monaco",
    "monterrey": "Monterrey, Mexico",
    "montpellier": "Montpellier, France",
    "montreal": "Montreal, Canada",
    "munich": "Munich, Germany",
    "nanchang": "Nanchang, China",
    "newport": "Newport, United States",
    "ningbo": "Ningbo, China",
    "nottingham": "Nottingham, United Kingdom",
    "osaka": "Osaka, Japan",
    "ostrava": "Ostrava, Czechia",
    "palermo": "Palermo, Italy",
    "paris-olympics": "Paris, France",
    "prague": "Prague, Czechia",
    "pune": "Pune, India",
    "queen-s-club": "London, United Kingdom",
    "rabat": "Rabat, Morocco",
    "rio-de-janeiro": "Rio de Janeiro, Brazil",
    "riyadh": "Riyadh, Saudi Arabia",
    "rome": "Rome, Italy",
    "rotterdam": "Rotterdam, Netherlands",
    "rouen": "Rouen, France",
    "samsun": "Samsun, Türkiye",
    "san-diego": "San Diego, United States",
    "santiago": "Santiago, Chile",
    "sao-paulo": "São Paulo, Brazil",
    "seoul": "Seoul, South Korea",
    "singapore": "Singapore",
    "sofia": "Sofia, Bulgaria",
    "stockholm": "Stockholm, Sweden",
    "strasbourg": "Strasbourg, France",
    "stuttgart": "Stuttgart, Germany",
    "tokyo": "Tokyo, Japan",
    "toronto": "Toronto, Canada",
    "umag": "Umag, Croatia",
    "vienna": "Vienna, Austria",
    "warsaw": "Warsaw, Poland",
    "washington": "Washington, United States",
    "winston-salem": "Winston-Salem, United States",
    "wuhan": "Wuhan, China",
    "zhengzhou": "Zhengzhou, China",
    "zhuhai": "Zhuhai, China",
    "hertogenbosch": "'s-Hertogenbosch, Netherlands",
    "tauste": "Tauste, Spain",
    "koksijde": "Koksijde, Belgium",
    "leipzig": "Leipzig, Germany",
    "macon": "Mâcon, France",
    "palma-del-rio": "Palma del Río, Spain",
    "pazardzhik": "Pazardzhik, Bulgaria",
    "paris-masters": "Paris, France",
    "indian-wells": "Indian Wells, United States",
    "miami-open": "Miami, United States",
    "monte-carlo": "Monte Carlo, Monaco",
    "madrid-open": "Madrid, Spain",
    "italian-open": "Rome, Italy",
    "rome-masters": "Rome, Italy",
    "canadian-open": "Toronto / Montreal, Canada",
    "canada-masters": "Toronto / Montreal, Canada",
    "cincinnati-open": "Cincinnati, United States",
    "shanghai-masters": "Shanghai, China",
    "doha": "Doha, Qatar",
    "dubai": "Dubai, United Arab Emirates",
    "laver-cup": "Host city varies by edition",
    "next-gen-finals": "Jeddah, Saudi Arabia",
    "nextgen-finals": "Jeddah, Saudi Arabia",
    "tour-finals": "Turin, Italy",
    "united-cup": "Perth / Sydney, Australia",
    "bjk-cup-finals": "Shenzhen, China",
    "bjk-cup-qualifiers": "Multiple host cities",
    "bjk-cup-playoffs": "Multiple host cities",
}


TEAM_COUNTRIES = {
    "ARG": "Argentina", "AUS": "Australia", "AUT": "Austria", "BAR": "Barbados",
    "BEL": "Belgium", "BEN": "Benin", "BER": "Bermuda", "BIH": "Bosnia and Herzegovina",
    "BOL": "Bolivia", "BRA": "Brazil", "BUL": "Bulgaria", "CAN": "Canada",
    "CHI": "Chile", "CHN": "China", "COL": "Colombia", "CRC": "Costa Rica",
    "CRO": "Croatia", "CYP": "Cyprus", "CZE": "Czechia", "DEN": "Denmark",
    "DOM": "Dominican Republic", "ECU": "Ecuador", "EGY": "Egypt", "ESA": "El Salvador",
    "ESP": "Spain", "EST": "Estonia", "FIN": "Finland", "FRA": "France",
    "GBR": "United Kingdom", "GEO": "Georgia", "GER": "Germany", "GRE": "Greece",
    "GUA": "Guatemala", "HKG": "Hong Kong", "HUN": "Hungary", "INA": "Indonesia",
    "IND": "India", "IRI": "Iran", "IRL": "Ireland", "ISR": "Israel",
    "ITA": "Italy", "JAM": "Jamaica", "JOR": "Jordan", "JPN": "Japan",
    "KAZ": "Kazakhstan", "KOR": "South Korea", "KOS": "Kosovo", "KSA": "Saudi Arabia",
    "LAT": "Latvia", "LBN": "Lebanon", "LTU": "Lithuania", "LUX": "Luxembourg",
    "MAR": "Morocco", "MDA": "Moldova", "MEX": "Mexico", "MKD": "North Macedonia",
    "MLT": "Malta", "MNE": "Montenegro", "MON": "Monaco", "NAM": "Namibia",
    "NED": "Netherlands", "NGR": "Nigeria", "NOR": "Norway", "NZL": "New Zealand",
    "PAK": "Pakistan", "PAR": "Paraguay", "PER": "Peru", "POL": "Poland",
    "POR": "Portugal", "PUR": "Puerto Rico", "ROU": "Romania", "RSA": "South Africa",
    "SEN": "Senegal", "SLO": "Slovenia", "SRB": "Serbia", "SUI": "Switzerland",
    "SVK": "Slovakia", "SWE": "Sweden", "SYR": "Syria", "THA": "Thailand",
    "TOG": "Togo", "TPE": "Chinese Taipei", "TUN": "Tunisia", "TUR": "Türkiye",
    "UKR": "Ukraine", "URU": "Uruguay", "USA": "United States", "UZB": "Uzbekistan",
    "VEN": "Venezuela", "VIE": "Vietnam", "ZIM": "Zimbabwe",
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
    if "location" not in profile and normalized_name.startswith(("davis-cup", "bjk-cup")):
        host = re.search(r":\s*([A-Z]{3})\s+vs\b", name or "", re.IGNORECASE)
        if host:
            code = host.group(1).upper()
            profile["location"] = TEAM_COUNTRIES.get(code, f"Host nation: {code}")
        else:
            profile["location"] = "Multiple host cities"
    profile.setdefault("location", "Venue to be confirmed")
    profile.setdefault("founded", None)
    profile.setdefault(
        "summary",
        "Explore this tournament through the results, champions and seasons preserved in the Tennisd archive.",
    )
    profile.setdefault("official_url", None)
    return profile
