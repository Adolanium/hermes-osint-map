"""A short, hand-written list of maritime chokepoints.

Facts are limited to geography and well-established descriptions (what a passage connects, who
borders it, what kind of traffic uses it). There are deliberately no traffic volumes or shares:
those change year to year and this list has no source that keeps them current.
"""

from __future__ import annotations

from .parsers import item

# id, name, kind, lat, lon, country (when one state holds both shores), connects, borders, note, wiki
_POINTS = [
    ("hormuz", "Strait of Hormuz", "strait", 26.57, 56.25, None, "Persian Gulf and Gulf of Oman", "Iran, Oman",
     "The only sea exit for oil and LNG exports from most Persian Gulf producers.", "Strait_of_Hormuz"),
    ("bab-el-mandeb", "Bab-el-Mandeb", "strait", 12.58, 43.33, None, "Red Sea and Gulf of Aden",
     "Yemen, Djibouti, Eritrea", "Southern gate of the Red Sea route between Europe and Asia via Suez.", "Bab-el-Mandeb"),
    ("suez", "Suez Canal", "canal", 30.58, 32.33, "EG", "Mediterranean Sea and Red Sea", "Egypt",
     "Sea-level canal opened in 1869; the shortest sea route between Europe and Asia.", "Suez_Canal"),
    ("malacca", "Strait of Malacca", "strait", 2.5, 101.4, None, "Andaman Sea and South China Sea",
     "Malaysia, Indonesia, Singapore", "Main shipping lane between the Indian Ocean and East Asia.", "Strait_of_Malacca"),
    ("panama", "Panama Canal", "canal", 9.08, -79.68, "PA", "Caribbean Sea and Pacific Ocean", "Panama",
     "Lock canal opened in 1914; its locks are fed by Gatun Lake, so drought limits transits.", "Panama_Canal"),
    ("bosporus", "Bosporus", "strait", 41.12, 29.07, "TR", "Black Sea and Sea of Marmara", "Turkey",
     "Runs through Istanbul; passage is governed by the 1936 Montreux Convention.", "Bosporus"),
    ("gibraltar", "Strait of Gibraltar", "strait", 35.96, -5.6, None, "Atlantic Ocean and Mediterranean Sea",
     "Spain, Morocco (and the British territory of Gibraltar)", "The Mediterranean's only natural opening to the Atlantic.",
     "Strait_of_Gibraltar"),
    ("dover", "Strait of Dover", "strait", 51.0, 1.45, None, "English Channel and North Sea", "United Kingdom, France",
     "Narrowest point of the Channel, run under a traffic separation scheme.", "Strait_of_Dover"),
    ("taiwan", "Taiwan Strait", "strait", 24.2, 119.6, None, "East China Sea and South China Sea", "Taiwan, China",
     "Main north-south route along the East Asian coast.", "Taiwan_Strait"),
    ("lombok", "Lombok Strait", "strait", -8.77, 115.75, "ID", "Bali Sea and Indian Ocean", "Indonesia (Bali, Lombok)",
     "Deep-water alternative to Malacca for ships too large for its shallows.", "Lombok_Strait"),
    ("danish-straits", "Danish straits", "strait", 55.5, 10.95, None, "Baltic Sea and Kattegat (North Sea)",
     "Denmark, Sweden", "The Great Belt, Little Belt and Oresund: the only sea route out of the Baltic.", "Danish_straits"),
    ("good-hope", "Cape of Good Hope", "cape", -34.36, 18.47, "ZA", "Atlantic Ocean and Indian Ocean", "South Africa",
     "The route around Africa that ships take when Suez or the Red Sea is closed to them.", "Cape_of_Good_Hope"),
    ("kerch", "Kerch Strait", "strait", 45.3, 36.5, None, "Sea of Azov and Black Sea", "Russia, Ukraine (Crimea)",
     "The Sea of Azov's only outlet; spanned by the Crimean Bridge since 2018.", "Kerch_Strait"),
    ("sunda", "Sunda Strait", "strait", -6.0, 105.8, "ID", "Java Sea and Indian Ocean", "Indonesia (Java, Sumatra)",
     "Shallow, volcanic passage between Java and Sumatra used as a shortcut to Malacca.", "Sunda_Strait"),
    ("magellan", "Strait of Magellan", "strait", -53.5, -70.5, "CL", "Atlantic Ocean and Pacific Ocean", "Chile",
     "Sheltered passage around South America, south of the mainland.", "Strait_of_Magellan"),
]


def items() -> list[dict]:
    return [
        item("chokepoints", key, kind, lat, lon, name,
             subtitle=connects, severity=0.5, country=country,
             url=f"https://en.wikipedia.org/wiki/{wiki}",
             rows=[("Connects", connects), ("Borders", borders), ("Notes", note)])
        for key, name, kind, lat, lon, country, connects, borders, note, wiki in _POINTS
    ]
