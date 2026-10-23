"""Country codes and small geometry helpers.

Sources name countries four different ways: ISO alpha-2 (the page's language), ISO alpha-3 (GDACS),
FIPS 10-4 (GDELT's geo fields) and English names (USGS place strings, "12 km SW of X, Japan").
One table maps all of them to alpha-2. It covers sovereign states and the territories that show
up in those feeds; anything else maps to ``None`` rather than a guess.
"""

from __future__ import annotations

import math
from typing import Iterable

# iso2 iso3 fips name
_TABLE = """
AF AFG AF Afghanistan
AL ALB AL Albania
DZ DZA AG Algeria
AS ASM AQ American Samoa
AD AND AN Andorra
AO AGO AO Angola
AI AIA AV Anguilla
AQ ATA AY Antarctica
AG ATG AC Antigua and Barbuda
AR ARG AR Argentina
AM ARM AM Armenia
AW ABW AA Aruba
AU AUS AS Australia
AT AUT AU Austria
AZ AZE AJ Azerbaijan
BS BHS BF Bahamas
BH BHR BA Bahrain
BD BGD BG Bangladesh
BB BRB BB Barbados
BY BLR BO Belarus
BE BEL BE Belgium
BZ BLZ BH Belize
BJ BEN BN Benin
BM BMU BD Bermuda
BT BTN BT Bhutan
BO BOL BL Bolivia
BA BIH BK Bosnia and Herzegovina
BW BWA BC Botswana
BR BRA BR Brazil
BN BRN BX Brunei
BG BGR BU Bulgaria
BF BFA UV Burkina Faso
BI BDI BY Burundi
KH KHM CB Cambodia
CM CMR CM Cameroon
CA CAN CA Canada
CV CPV CV Cabo Verde
KY CYM CJ Cayman Islands
CF CAF CT Central African Republic
TD TCD CD Chad
CL CHL CI Chile
CN CHN CH China
CO COL CO Colombia
KM COM CN Comoros
CG COG CF Republic of the Congo
CD COD CG Democratic Republic of the Congo
CK COK CW Cook Islands
CR CRI CS Costa Rica
CI CIV IV Cote d'Ivoire
HR HRV HR Croatia
CU CUB CU Cuba
CW CUW UC Curacao
CY CYP CY Cyprus
CZ CZE EZ Czechia
DK DNK DA Denmark
DJ DJI DJ Djibouti
DM DMA DO Dominica
DO DOM DR Dominican Republic
EC ECU EC Ecuador
EG EGY EG Egypt
SV SLV ES El Salvador
GQ GNQ EK Equatorial Guinea
ER ERI ER Eritrea
EE EST EN Estonia
SZ SWZ WZ Eswatini
ET ETH ET Ethiopia
FK FLK FK Falkland Islands
FO FRO FO Faroe Islands
FJ FJI FJ Fiji
FI FIN FI Finland
FR FRA FR France
GF GUF FG French Guiana
PF PYF FP French Polynesia
GA GAB GB Gabon
GM GMB GA Gambia
GE GEO GG Georgia
DE DEU GM Germany
GH GHA GH Ghana
GI GIB GI Gibraltar
GR GRC GR Greece
GL GRL GL Greenland
GD GRD GJ Grenada
GP GLP GP Guadeloupe
GU GUM GQ Guam
GT GTM GT Guatemala
GG GGY GK Guernsey
GN GIN GV Guinea
GW GNB PU Guinea-Bissau
GY GUY GY Guyana
HT HTI HA Haiti
HN HND HO Honduras
HK HKG HK Hong Kong
HU HUN HU Hungary
IS ISL IC Iceland
IN IND IN India
ID IDN ID Indonesia
IR IRN IR Iran
IQ IRQ IZ Iraq
IE IRL EI Ireland
IM IMN IM Isle of Man
IL ISR IS Israel
IT ITA IT Italy
JM JAM JM Jamaica
JP JPN JA Japan
JE JEY JE Jersey
JO JOR JO Jordan
KZ KAZ KZ Kazakhstan
KE KEN KE Kenya
KI KIR KR Kiribati
KP PRK KN North Korea
KR KOR KS South Korea
XK XKX KV Kosovo
KW KWT KU Kuwait
KG KGZ KG Kyrgyzstan
LA LAO LA Laos
LV LVA LG Latvia
LB LBN LE Lebanon
LS LSO LT Lesotho
LR LBR LI Liberia
LY LBY LY Libya
LI LIE LS Liechtenstein
LT LTU LH Lithuania
LU LUX LU Luxembourg
MO MAC MC Macau
MG MDG MA Madagascar
MW MWI MI Malawi
MY MYS MY Malaysia
MV MDV MV Maldives
ML MLI ML Mali
MT MLT MT Malta
MH MHL RM Marshall Islands
MQ MTQ MB Martinique
MR MRT MR Mauritania
MU MUS MP Mauritius
YT MYT MF Mayotte
MX MEX MX Mexico
FM FSM FM Micronesia
MD MDA MD Moldova
MC MCO MN Monaco
MN MNG MG Mongolia
ME MNE MJ Montenegro
MS MSR MH Montserrat
MA MAR MO Morocco
MZ MOZ MZ Mozambique
MM MMR BM Myanmar
NA NAM WA Namibia
NR NRU NR Nauru
NP NPL NP Nepal
NL NLD NL Netherlands
NC NCL NC New Caledonia
NZ NZL NZ New Zealand
NI NIC NU Nicaragua
NE NER NG Niger
NG NGA NI Nigeria
NU NIU NE Niue
MK MKD MK North Macedonia
MP MNP CQ Northern Mariana Islands
NO NOR NO Norway
OM OMN MU Oman
PK PAK PK Pakistan
PW PLW PS Palau
PS PSE WE Palestine
PA PAN PM Panama
PG PNG PP Papua New Guinea
PY PRY PA Paraguay
PE PER PE Peru
PH PHL RP Philippines
PN PCN PC Pitcairn Islands
PL POL PL Poland
PT PRT PO Portugal
PR PRI RQ Puerto Rico
QA QAT QA Qatar
RE REU RE Reunion
RO ROU RO Romania
RU RUS RS Russia
RW RWA RW Rwanda
SH SHN SH Saint Helena
KN KNA SC Saint Kitts and Nevis
LC LCA ST Saint Lucia
PM SPM SB Saint Pierre and Miquelon
VC VCT VC Saint Vincent and the Grenadines
WS WSM WS Samoa
SM SMR SM San Marino
ST STP TP Sao Tome and Principe
SA SAU SA Saudi Arabia
SN SEN SG Senegal
RS SRB RI Serbia
SC SYC SE Seychelles
SL SLE SL Sierra Leone
SG SGP SN Singapore
SX SXM NN Sint Maarten
SK SVK LO Slovakia
SI SVN SI Slovenia
SB SLB BP Solomon Islands
SO SOM SO Somalia
ZA ZAF SF South Africa
GS SGS SX South Georgia and the South Sandwich Islands
SS SSD OD South Sudan
ES ESP SP Spain
LK LKA CE Sri Lanka
SD SDN SU Sudan
SR SUR NS Suriname
SJ SJM SV Svalbard
SE SWE SW Sweden
CH CHE SZ Switzerland
SY SYR SY Syria
TW TWN TW Taiwan
TJ TJK TI Tajikistan
TZ TZA TZ Tanzania
TH THA TH Thailand
TL TLS TT Timor-Leste
TG TGO TO Togo
TK TKL TL Tokelau
TO TON TN Tonga
TT TTO TD Trinidad and Tobago
TN TUN TS Tunisia
TR TUR TU Turkey
TM TKM TX Turkmenistan
TC TCA TK Turks and Caicos Islands
TV TUV TV Tuvalu
UG UGA UG Uganda
UA UKR UP Ukraine
AE ARE AE United Arab Emirates
GB GBR UK United Kingdom
US USA US United States
UY URY UY Uruguay
UZ UZB UZ Uzbekistan
VU VUT NH Vanuatu
VA VAT VT Vatican City
VE VEN VE Venezuela
VN VNM VM Vietnam
VG VGB VI British Virgin Islands
VI VIR VQ U.S. Virgin Islands
WF WLF WF Wallis and Futuna
EH ESH WI Western Sahara
YE YEM YM Yemen
ZM ZMB ZA Zambia
ZW ZWE ZI Zimbabwe
"""

_ISO3: dict[str, str] = {}
_FIPS: dict[str, str] = {"GZ": "PS"}  # FIPS splits Palestine into West Bank (WE) and Gaza (GZ)
_NAMES: dict[str, str] = {}
NAMES: dict[str, str] = {}  # alpha-2 → display name
for _line in _TABLE.strip().splitlines():
    _iso2, _iso3, _fips, _name = _line.split(" ", 3)
    _ISO3[_iso3] = _iso2
    _FIPS[_fips] = _iso2
    NAMES[_iso2] = _name
    _NAMES[_name.lower()] = _iso2

# Other spellings the feeds use, plus US states and territories that USGS names instead of the
# country ("…, CA", "…, Alaska").
_ALIASES = {
    "usa": "US", "u.s.": "US", "united states of america": "US", "uk": "GB", "great britain": "GB",
    "england": "GB", "scotland": "GB", "wales": "GB", "northern ireland": "GB", "burma": "MM",
    "turkiye": "TR", "türkiye": "TR", "russian federation": "RU", "south korea": "KR",
    "republic of korea": "KR", "korea, south": "KR", "north korea": "KP", "korea, north": "KP",
    "ivory coast": "CI", "côte d'ivoire": "CI", "cape verde": "CV", "swaziland": "SZ",
    "east timor": "TL", "czech republic": "CZ", "macedonia": "MK", "drc": "CD", "dr congo": "CD",
    "congo": "CG", "congo, democratic republic of the": "CD", "the democratic republic of the congo": "CD",
    "vatican": "VA", "holy see": "VA", "micronesia, federated states of": "FM", "laos pdr": "LA",
    "lao pdr": "LA", "syrian arab republic": "SY", "viet nam": "VN", "iran, islamic republic of": "IR",
    "bolivia, plurinational state of": "BO", "venezuela, bolivarian republic of": "VE",
    "tanzania, united republic of": "TZ", "moldova, republic of": "MD", "palestinian territory": "PS",
    "west bank": "PS", "gaza": "PS", "gaza strip": "PS", "méxico": "MX", "the bahamas": "BS",
    "the gambia": "GM", "the netherlands": "NL", "falkland islands (islas malvinas)": "FK",
    "south sandwich islands": "GS", "south georgia": "GS", "reunion": "RE", "réunion": "RE",
    "curacao": "CW", "curaçao": "CW", "u.s. virgin islands": "VI", "northern mariana islands": "MP",
}
_US_STATES = (
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY "
    "NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC"
).split()
_US_STATE_NAMES = (
    "alabama alaska arizona arkansas california colorado connecticut delaware florida georgia hawaii idaho "
    "illinois indiana iowa kansas kentucky louisiana maine maryland massachusetts michigan minnesota "
    "mississippi missouri montana nebraska nevada ohio oklahoma oregon pennsylvania tennessee texas utah "
    "vermont virginia washington wisconsin wyoming"
).split() + ["new hampshire", "new jersey", "new mexico", "new york", "north carolina", "north dakota",
             "rhode island", "south carolina", "south dakota", "west virginia"]


def normalise_iso2(code: object) -> str | None:
    if not isinstance(code, str):
        return None
    code = code.strip().upper()
    return code if code in NAMES else None


def from_iso3(code: object) -> str | None:
    return _ISO3.get(code.strip().upper()) if isinstance(code, str) else None


def from_fips(code: object) -> str | None:
    return _FIPS.get(code.strip().upper()) if isinstance(code, str) else None


def from_name(name: object) -> str | None:
    """An English country name (or a US state, which maps to US) → alpha-2."""
    if not isinstance(name, str):
        return None
    key = " ".join(name.strip().lower().split())
    if not key:
        return None
    if key in _NAMES:
        return _NAMES[key]
    if key in _ALIASES:
        return _ALIASES[key]
    if key in _US_STATE_NAMES or name.strip() in _US_STATES:
        return "US"
    if key.endswith(" region"):  # USGS: "Kermadec Islands region", "Fiji region"
        return from_name(key[: -len(" region")])
    return None


def from_place(place: object) -> str | None:
    """The country in a trailing ", Country" of a place description (USGS, GDELT full names)."""
    if not isinstance(place, str) or not place.strip():
        return None
    tail = place.rsplit(",", 1)[-1].strip()
    return from_name(tail)


def name(code: str | None) -> str | None:
    return NAMES.get(code) if code else None


# --- Geometry --------------------------------------------------------------------------------------

EARTH_KM = 6371.0088


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_KM * math.asin(min(1.0, math.sqrt(a)))


def _points(coords: object) -> Iterable[tuple[float, float]]:
    """Every [lon, lat] pair inside nested GeoJSON coordinate arrays."""
    if isinstance(coords, (list, tuple)):
        if len(coords) >= 2 and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in coords[:2]):
            yield float(coords[0]), float(coords[1])
        else:
            for part in coords:
                yield from _points(part)


def centroid(geometry: object) -> tuple[float, float] | None:
    """(lat, lon) for a GeoJSON Point, or the vertex average of a (Multi)Polygon / LineString.

    The vertex average is not the true area centroid, but for alert areas and fire perimeters it
    lands inside or next to the shape, which is all a map marker needs. Rings repeat their first
    vertex at the end; that duplicate is skipped so it does not pull the average."""
    if not isinstance(geometry, dict):
        return None
    kind, coords = geometry.get("type"), geometry.get("coordinates")
    if kind == "Point":
        pts = list(_points(coords))[:1]
    elif kind in ("Polygon", "MultiPolygon", "LineString", "MultiLineString", "MultiPoint"):
        pts = []
        rings = coords if kind != "MultiPolygon" else [ring for poly in coords or [] for ring in poly or []]
        if kind in ("LineString", "MultiPoint"):
            rings = [coords]
        for ring in rings or []:
            ring_pts = list(_points(ring))
            if len(ring_pts) > 1 and ring_pts[0] == ring_pts[-1]:
                ring_pts = ring_pts[:-1]
            pts.extend(ring_pts)
    else:
        return None
    pts = [(lon, lat) for lon, lat in pts if -180 <= lon <= 180 and -90 <= lat <= 90]
    if not pts:
        return None
    lat = sum(p[1] for p in pts) / len(pts)
    # Average longitudes on the circle so a shape across the antimeridian does not land at 0°.
    x = sum(math.cos(math.radians(p[0])) for p in pts)
    y = sum(math.sin(math.radians(p[0])) for p in pts)
    lon = math.degrees(math.atan2(y, x)) if (x or y) else pts[0][0]
    return round(lat, 5), round(lon, 5)
