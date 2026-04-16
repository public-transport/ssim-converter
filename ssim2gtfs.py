#!/usr/bin/env python3
#
# SPDX-FileCopyrightText: 2026 Volker Krause <vkrause@kde.org>
# SPDX-License-Identifier: AGPL-3.0-or-later

import argparse
import csv
import io
import json
import re
import requests
import os
import sys
import urllib.parse
import zipfile


agencies = {}
stops = {}
routes = {}
calendar = []
trips = []
stoptimes = []
translations = []
transfers = {}

wikidata_airlines = {}
wikidata_airports = {}
wikidata_terminals = {}

airline_errors = {}
airport_errors = {}
terminal_errors = {}


parser = argparse.ArgumentParser(description='SSIM to GTFS converter.')
parser.add_argument('--ssim', required=True, help='Path to SSIM input file.')
parser.add_argument('--out', required=True, help='Path to GTFS output file.')
arguments = parser.parse_args()


def query_wikidata(sparql: str, cache_name: str):
    if os.path.exists(cache_name):
        print(f"Reusing cached data for {cache_name}…")
        return json.load(open(cache_name, "r"))

    print(f"Querying Wikidata for {cache_name}…")
    req = requests.get(f"https://query.wikidata.org/sparql?{urllib.parse.urlencode({
                        "query": sparql,
                        "format": "json",
                        })}",
                       headers={"User-Agent": "org.transitous.ssim-converter (vkrause@kde.org)"})
    with open(cache_name, "wb") as f:
        f.write(req.content)

    return req.json()


def parse_wikidata_id(data):
    uri = data["item"]["value"]
    return uri[uri.rfind("/")+1:]


# TODO this gives us Cargo subsidiaries with the same IATA designator as well!
def parse_wikidata_airlines(data):
    for a in data["results"]["bindings"]:
        if "dissolved" in a:
            continue
        iata = a["iataCode"]["value"]
        if iata not in wikidata_airlines:
            wikidata_airlines[iata] = {
                "name": {},
                "url": a["url"]["value"] if "url" in a else None,
                "wikidata": parse_wikidata_id(a),
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
                "wikidata": parse_wikidata_id(a),
            }
        lang = a["label"]["xml:lang"]
        if len(lang) > 2 and lang[2] == "-":
            lang = lang[0:2]
        if len(lang) == 2:
            wikidata_airports[iata_code]["name"][lang] = a["label"]["value"]


def parse_wikidata_terminals(data):
    for t in data["results"]["bindings"]:
        iata_code = t["iataCode"]["value"]
        if iata_code not in wikidata_terminals:
            wikidata_terminals[iata_code] = []
        coord = parse_wikidata_coordinate(t["coord"]["value"])
        wikidata_terminals[iata_code].append({
            "name": t["itemLabel"]["value"],
            "lon": coord[0],
            "lat": coord[1],
            "wikidata": parse_wikidata_id(t),
        })


def find_terminal(iata_code: str, terminal: str):
    for t in wikidata_terminals.get(iata_code, []):
        if t["name"].lower().endswith(f"terminal {terminal}") or f"terminal {terminal} " in t["name"].lower():
            return t
    if len(terminal) == 1 and terminal[0].isalpha():
        for t in wikidata_terminals.get(iata_code, []):
            if re.search(f"(^|\\b){terminal}[a-z]+ terminal", t["name"], flags=re.IGNORECASE):
                return t
    if len(terminal) == 1 and terminal[0].isdecimal():
        for t in wikidata_terminals.get(iata_code, []):
            if t["name"].endswith(f"T{terminal}"):
                return t
    return None


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


def parse_ssim_date(ssim_date: str):
    return f"20{ssim_date[5:7]}{month_map[ssim_date[2:5]]}{ssim_date[0:2]}"


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
        "wikidata": wd["wikidata"],
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
    wd = wikidata_airports[iata_code]
    if iata_code not in stops:
        stops[iata_code] = {
            "stop_id": iata_code,
            "stop_name": f"{wd["name"]["en"]} ({iata_code})" if "en" in wd["name"] else iata_code,
            "stop_lat": wd["lat"],
            "stop_lon": wd["lon"],
            "location_type": 0,
            "stop_timezone": wd["tz"],
            "stop_url": wd["url"],
            "wikidata": wd["wikidata"],
        }
        for (lang, name) in wd["name"].items():
            if lang != "en" and wd["name"].get("en", "") == name:
                continue
            translations.append({
                "table_name": "stops",
                "field_name": "stop_name",
                "language": lang,
                "record_id": iata_code,
                "translation": f"{name} ({iata_code})"
            })
        # minimum 60 min transfer times to avoid nonsense transfers
        transfers[iata_code] = {
            "from_stop_id": iata_code,
            "to_stop_id": iata_code,
            "transfer_type": 2,
            "min_transfer_time": 3600,
        }
    terminal_code = f"{iata_code}_{terminal}"
    if terminal == "" or terminal_code in stops:
        return
    wdt = find_terminal(iata_code, terminal)
    if not wdt and len(terminal) == 2 and terminal[0].isdecimal() and terminal[1].isalpha():
        wdt = find_terminal(iata_code, terminal[:1])
    if not wdt and iata_code in wikidata_terminals:
        print(f"No terminal data found for {iata_code} Terminal {terminal} despite terminal data being available")
    elif not wdt and terminal_code not in terminal_errors:
        terminal_errors[terminal_code] = None
        print(f"No terminal data found for {iata_code} Terminal {terminal}")
    stops[terminal_code] = {
        "stop_id": terminal_code,
        "stop_name": stops[iata_code]["stop_name"],
        "platform_code": f"Terminal {terminal}",
        "stop_lat": wdt["lat"] if wdt else wd["lat"],
        "stop_lon": wdt["lon"] if wdt else wd["lon"],
        "location_type": 0,
        "parent_station": iata_code,
        "stop_timezone": wd["tz"],
        "wikidata": wdt["wikidata"] if wdt else None,
    }
    transfers[terminal_code] = {
        "from_stop_id": terminal_code,
        "to_stop_id": terminal_code,
        "transfer_type": 2,
        "min_transfer_time": 3600,
    }
    stops[iata_code]["location_type"] = 1
    for (lang, name) in wd["name"].items():
        if lang != "en" and wd["name"].get("en", "") == name:
            continue
        translations.append({
            "table_name": "stops",
            "field_name": "stop_name",
            "language": lang,
            "record_id": terminal_code,
            "translation": f"{name} ({iata_code})"
        })


gtfs_columns = {
    "agency.txt": ["agency_id", "agency_name", "agency_url", "agency_timezone", "agency_lang", "wikidata"],
    "stops.txt": ["stop_id", "stop_name", "platform_code", "stop_lat", "stop_lon", "location_type", "parent_station", "stop_timezone", "stop_url", "wikidata"],
    "routes.txt": ["route_id", "agency_id", "route_short_name", "route_type"],
    "calendar.txt": ["service_id", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "start_date", "end_date"],
    "trips.txt": ["route_id", "service_id", "trip_id", "trip_headsign", "trip_short_name", "cars_allowed"],
    "stop_times.txt": ["trip_id", "departure_time", "arrival_time", "stop_id", "stop_sequence"],
    "translations.txt": ["table_name", "field_name", "language", "record_id", "translation"],
    "feed_info.txt": ["feed_publisher_name", "feed_publisher_url", "feed_lang", "feed_start_date", "feed_end_date"],
    "transfers.txt": ["from_stop_id", "to_stop_id", "transfer_type", "min_transfer_time"],
}


def write_gtfs_file(gtfs_zip, file_name: str, data):
    string_buffer = io.StringIO()
    writer = csv.DictWriter(string_buffer, fieldnames=gtfs_columns[file_name])
    writer.writeheader()
    if isinstance(data, dict):
        for (_, row) in data.items():
            writer.writerow(row)
    else:
        for row in data:
            writer.writerow(row)
    gtfs_zip.writestr(file_name, string_buffer.getvalue())


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

terminal_sparql = """
SELECT ?item ?itemLabel ?iataCode ?coord
WHERE
{
  ?item wdt:P31 wd:Q849706.
  ?item (wdt:P361*) ?airport.
  ?airport wdt:P238 ?iataCode.
  ?item wdt:P625 ?coord.
  SERVICE wikibase:label { bd:serviceParam wikibase:language "[AUTO_LANGUAGE],mul,en". }
}
"""
parse_wikidata_terminals(query_wikidata(terminal_sparql, "terminals"))


with open(arguments.ssim) as f:
    for line in f:
        if line[0] == '2':
            if line[1] != 'U':
                print("SSIM data using local time is not supported yet!")
                sys.exit(1)
            feed_info = [{
                "feed_publisher_name": "Transitous",
                "feed_publisher_url": "https://transitous.org",
                "feed_lang": "en",
                "feed_start_date": parse_ssim_date(line[14:21]),
                "feed_end_date": parse_ssim_date(line[21:28]),
            }]
        if line[0] == '3':
            agency_id = line[128:131].strip() if line[128:131].strip() != "" else line[2:5].strip()
            add_agency(agency_id)
            from_airport = line[36:39]
            from_terminal = line[52:54].strip()
            to_airport = line[54:57]
            to_terminal = line[70:72].strip()
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
            calendar.append({
                "service_id": trip_id,
                "monday": 1 if line[28] == "1" else 0,
                "tuesday": 1 if line[29] == "2" else 0,
                "wednesday": 1 if line[30] == "3" else 0,
                "thursday": 1 if line[31] == "4" else 0,
                "friday": 1 if line[32] == "5" else 0,
                "saturday": 1 if line[33] == "6" else 0,
                "sunday": 1 if line[34] == "7" else 0,
                "start_date": parse_ssim_date(line[14:21]),
                "end_date": parse_ssim_date(line[21:28]),
            })
            trips.append({
                "route_id": route_id,
                "service_id": trip_id,
                "trip_id": trip_id,
                "trip_headsign": stops[to_airport]["stop_name"] if to_airport in stops else None,  # TODO translations?
                "trip_short_name": line[2:9],  # TODO normalize/clean
                "cars_allowed": 2,
            })
            # times need to be in UTC!
            dep_time = (line[39:41], line[41:43])
            arr_time = (line[61:63], line[63:65])
            if arr_time < dep_time:
                arr_time = (int(arr_time[0]) + 24, arr_time[1])
            stoptimes.append({
                "trip_id": trip_id,
                "arrival_time": f"{dep_time[0]}:{dep_time[1]}:00",
                "departure_time": f"{dep_time[0]}:{dep_time[1]}:00",
                "stop_id": f"{from_airport}_{from_terminal}" if from_terminal != "" else from_airport,
                "stop_sequence": 1,
            })
            stoptimes.append({
                "trip_id": trip_id,
                "arrival_time": f"{arr_time[0]}:{arr_time[1]}:00",
                "departure_time": f"{arr_time[0]}:{arr_time[1]}:00",
                "stop_id": f"{to_airport}_{to_terminal}" if to_terminal != "" else to_airport,
                "stop_sequence": 2,
            })


# write GTFS
with zipfile.ZipFile(arguments.out, 'w') as z:
    write_gtfs_file(z, "agency.txt", agencies)
    write_gtfs_file(z, "stops.txt", stops)
    write_gtfs_file(z, "routes.txt", routes)
    write_gtfs_file(z, "calendar.txt", calendar)
    write_gtfs_file(z, "trips.txt", trips)
    write_gtfs_file(z, "stop_times.txt", stoptimes)
    write_gtfs_file(z, "translations.txt", translations)
    write_gtfs_file(z, "feed_info.txt", feed_info)
    write_gtfs_file(z, "transfers.txt", transfers)
