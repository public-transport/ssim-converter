#!/usr/bin/env python3
#
# SPDX-FileCopyrightText: 2026 Volker Krause <vkrause@kde.org>
# SPDX-License-Identifier: AGPL-3.0-or-later

import argparse
import csv
import json
import requests
import os
import urllib.parse


agencies = {}
stops = {}
routes = {}
calendar = {}
trips = {}
stoptimes = []
translations = []

wikidata_airlines = {}
wikidata_airports = {}

airline_errors = {}
airport_errors = {}


def query_wikidata(sparql: str, cache_name: str):
    if os.path.exists(cache_name):
        print(f"Reusing cached data for {cache_name}…")
        return json.load(open(cache_name, "r"))

    req = requests.get(f"https://query.wikidata.org/sparql?{urllib.parse.urlencode({
                        "query": sparql,
                        "format": "json",
                        })}",
                       headers={"User-Agent": "org.transitous.ssim-converter (vkrause@kde.org)"})
    with open(cache_name, "wb") as f:
        f.write(req.content)

    return req.json()


# TODO this gives us Cargo subsidiaries with the same IATA designator as well!
def parse_wikidata_airlines(data):
    for a in data["results"]["bindings"]:
        if "dissolved" in a:
            continue
        iata = a["iataCode"]["value"]
        if iata not in wikidata_airlines:
            wikidata_airlines[iata] = {
                "name": {},
                "url": a["url"]["value"] if "url" in a else None
            }
        lang = a["label"]["xml:lang"]
        if len(lang) > 2 and lang[2] == "-":
            lang = lang[0:2]
        if len(lang) == 2:
            wikidata_airlines[iata]["name"][lang] = a["label"]["value"]

        if "icaoCode" in a:
            icao = a["icaoCode"]["value"]
            wikidata_airlines[icao] = wikidata_airlines[iata]


def parse_wikidata_coordinate(coord):
    if not coord.startswith("Point("):
        return None
    idx = coord.find(" ")
    return (float(coord[6:idx]), float(coord[idx+1:-1]))


def parse_wikidata_airports(data):
    for a in data["results"]["bindings"]:
        iata_code = a["iataCode"]["value"]
        if iata_code not in wikidata_airports:
            coord = parse_wikidata_coordinate(a["coord"]["value"])
            if not coord:
                continue
            wikidata_airports[iata_code] = {
                "lon": coord[0],
                "lat": coord[1],
                "url": a["url"]["value"] if "url" in a else None,
                "tz": a["iana"]["value"],
                "name": {},
            }
        lang = a["label"]["xml:lang"]
        if len(lang) > 2 and lang[2] == "-":
            lang = lang[0:2]
        if len(lang) == 2:
            wikidata_airports[iata_code]["name"][lang] = a["label"]["value"]


month_map = {
    "JAN": "01",
    "FEB": "02",
    "MAR": "03",
    "APR": "04",
    "MAY": "05",
    "JUN": "06",
    "JUL": "07",
    "AUG": "08",
    "SEP": "09",
    "OCT": "10",
    "NOV": "11",
    "DEC": "12",
}


def add_agency(airline_code: str):
    if airline_code in agencies:
        return
    if airline_code not in wikidata_airlines:
        if airline_code in airline_errors:
            return
        print(f"No Wikidata entry for airline {airline_code} found!")
        airline_errors[airline_code] = None
        return
    wd = wikidata_airlines[airline_code]
    agencies[airline_code] = {
        "agency_id": airline_code,
        "agency_name": wd["name"]["en"] if "en" in wd["name"] else airline_code,
        "agency_url": wd["url"],
        "agency_timezone": "Etc/UTC",
        "agency_lang": "en",  # our default for Wikidata content
    }
    for (lang, name) in wd["name"].items():
        if lang != "en" and wd["name"].get("en", "") == name:
            continue
        translations.append({
            "table_name": "agency",
            "field_name": "agency_name",
            "language": lang,
            "record_id": airline_code,
            "translation": name,
        })


def add_stop(iata_code: str, terminal: str):
    if iata_code not in wikidata_airports:
        if iata_code in airport_errors:
            return
        print(f"No Wikidata entry for airport {iata_code} found!")
        airport_errors[iata_code] = None
        return
    if iata_code not in stops:
        stops[iata_code] = {
            "stop_id": iata_code,
            "stop_name": f"{wikidata_airports[iata_code]["name"]["en"]} ({iata_code})" if "en" in wikidata_airports[iata_code]["name"] else iata_code,
            "stop_lat": wikidata_airports[iata_code]["lat"],
            "stop_lon": wikidata_airports[iata_code]["lon"],
            "location_type": 0,
            "stop_timezone": wikidata_airports[iata_code]["tz"],
            "stop_url": wikidata_airports[iata_code]["url"],
        }
        for (lang, name) in wikidata_airports[iata_code]["name"].items():
            if lang != "en" and wikidata_airports[iata_code]["name"].get("en", "") == name:
                continue
            translations.append({
                "table_name": "stops",
                "field_name": "stop_name",
                "language": lang,
                "record_id": iata_code,
                "translation": f"{name} ({iata_code})"
            })
    if terminal == "" or f"{iata_code}_{terminal}" in stops:
        return
    stops[f"{iata_code}_{terminal}"] = {
        "stop_id": f"{iata_code}_{terminal}",
        "stop_name": f"{wikidata_airports[iata_code]["name"]["en"]} ({iata_code}) Terminal {terminal}" if "en" in wikidata_airports[iata_code]["name"] else iata_code,
        "stop_code": terminal,
        "stop_lat": wikidata_airports[iata_code]["lat"],  # TODO
        "stop_lon": wikidata_airports[iata_code]["lon"],  # TODO
        "location_type": 0,
        "parent_station": iata_code,
        "stop_timezone": wikidata_airports[iata_code]["tz"],
    }
    stops[iata_code]["location_type"] = 1
    for (lang, name) in wikidata_airports[iata_code]["name"].items():
        if lang != "en" and wikidata_airports[iata_code]["name"].get("en", "") == name:
            continue
        translations.append({
            "table_name": "stops",
            "field_name": "stop_name",
            "language": lang,
            "record_id": f"{iata_code}_{terminal}",
            "translation": f"{name} ({iata_code}) Terminal {terminal}"
        })


airline_sparql = """
SELECT DISTINCT ?item ?iataCode ?icaoCode ?label ?url ?dissolved
WHERE
{
  ?item (wdt:P31/wdt:P279*) wd:Q46970.
  ?item wdt:P229 ?iataCode.
  OPTIONAL { ?item wdt:P230 ?icaoCode }.
  ?item rdfs:label ?label.
  OPTIONAL { ?item wdt:P576 ?dissolved }.
  OPTIONAL { ?item wdt:P856 ?url }.
}
"""
parse_wikidata_airlines(query_wikidata(airline_sparql, "airlines"))

airport_sparql = """
SELECT DISTINCT ?item ?iataCode ?coord ?label ?url ?iana
WHERE
{
  ?item (wdt:P31/wdt:P279*) wd:Q62447.
  ?item wdt:P238 ?iataCode.
  ?item wdt:P625 ?coord.
  ?item rdfs:label ?label.
  ?item wdt:P421 ?tz.
  ?tz wdt:P6687 ?iana.
  OPTIONAL { ?item wdt:P856 ?url }.
}
"""
parse_wikidata_airports(query_wikidata(airport_sparql, "airports"))


with open("af-kl-to-hv-s26-w26.ssim") as f:
    for line in f:
        if line[0] == '3':
            agency_id = line[128:131].strip() if line[128:131].strip() != "" else line[2:5].strip()
            add_agency(agency_id)
            from_airport = line[36:39]
            from_terminal = line[52:53].strip()
            to_airport = line[54:57]
            to_terminal = line[70:71].strip()
            add_stop(from_airport, from_terminal)
            add_stop(to_airport, to_terminal)
            if from_airport not in wikidata_airports or to_airport not in wikidata_airports or agency_id not in wikidata_airlines:
                continue
            route_id = line[2:9].replace(' ', '_')
            if route_id not in routes:
                routes[route_id] = {
                    "route_id": route_id,
                    "agency_id": agency_id,
                    "route_short_name": f"{from_airport}-{to_airport}",
                    "route_type": 1100,
                }
            trip_id = line[2:12].replace(' ', '_')
            calendar[trip_id] = {
                "service_id": trip_id,
                "monday": 1 if line[28] == "1" else 0,
                "tuesday": 1 if line[29] == "2" else 0,
                "wednesday": 1 if line[30] == "3" else 0,
                "thursday": 1 if line[31] == "4" else 0,
                "friday": 1 if line[32] == "5" else 0,
                "saturday": 1 if line[33] == "6" else 0,
                "sunday": 1 if line[34] == "7" else 0,
                "start_date": f"20{line[19:21]}{month_map[line[16:19]]}{line[14:16]}",
                "end_date": f"20{line[26:28]}{month_map[line[23:26]]}{line[21:23]}",
            }
            trips[trip_id] = {
                "route_id": route_id,
                "service_id": trip_id,
                "trip_id": trip_id,
                "trip_headsign": stops[to_airport]["stop_name"] if to_airport in stops else None,  # TODO translations?
                "trip_short_name": line[2:9],  # TODO normalize/clean
                "cars_allowed": 2,
            }
            # TODO times need to be converted to UTC
            stoptimes.append({
                "trip_id": trip_id,
                "arrival_time": f"{line[39:41]}:{line[41:43]}:00",
                "departure_time": f"{line[39:41]}:{line[41:43]}:00",
                "stop_id": f"{from_airport}_{from_terminal}" if from_terminal != "" else from_airport,
                "stop_sequence": 1,
            })
            stoptimes.append({
                "trip_id": trip_id,
                "arrival_time": f"{line[61:63]}:{line[63:65]}:00",
                "departure_time": f"{line[61:63]}:{line[63:65]}:00",
                "stop_id": f"{to_airport}_{to_terminal}" if to_terminal != "" else to_airport,
                "stop_sequence": 2,
            })


# write GTFS
with open("agency.txt", 'w') as f:
    writer = csv.DictWriter(f, fieldnames=["agency_id", "agency_name", "agency_url", "agency_timezone", "agency_lang"])
    writer.writeheader()
    for (_, agency) in agencies.items():
        writer.writerow(agency)

with open("stops.txt", 'w') as f:
    writer = csv.DictWriter(f, fieldnames=["stop_id", "stop_name", "stop_code", "stop_lat", "stop_lon", "location_type", "parent_station", "stop_timezone", "stop_url"])
    writer.writeheader()
    for (_, stop) in stops.items():
        writer.writerow(stop)

with open("routes.txt", 'w') as f:
    writer = csv.DictWriter(f, fieldnames=["route_id", "agency_id", "route_short_name", "route_type"])
    writer.writeheader()
    for (_, route) in routes.items():
        writer.writerow(route)

with open("calendar.txt", 'w') as f:
    writer = csv.DictWriter(f, fieldnames=["service_id", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "start_date", "end_date"])
    writer.writeheader()
    for (_, entry) in calendar.items():
        writer.writerow(entry)

with open("trips.txt", 'w') as f:
    writer = csv.DictWriter(f, fieldnames=["route_id", "service_id", "trip_id", "trip_headsign", "trip_short_name", "cars_allowed"])
    writer.writeheader()
    for (_, trip) in trips.items():
        writer.writerow(trip)

with open("stop_times.txt", 'w') as f:
    writer = csv.DictWriter(f, fieldnames=["trip_id", "departure_time", "arrival_time", "stop_id", "stop_sequence"])
    writer.writeheader()
    for entry in stoptimes:
        writer.writerow(entry)

with open("translations.txt", 'w') as f:
    writer = csv.DictWriter(f, fieldnames=["table_name", "field_name", "language", "record_id", "translation"])
    writer.writeheader()
    for t in translations:
        writer.writerow(t)
