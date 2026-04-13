#!/usr/bin/env python3
#
# SPDX-FileCopyrightText: 2026 Volker Krause <vkrause@kde.org>
# SPDX-License-Identifier: AGPL-3.0-or-later

import argparse
import csv


agencies = {}
stops = {}
routes = {}
calendar = {}
trips = {}
stoptimes = []


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
    agencies[airline_code] = {
        "agency_id": airline_code,
        "agency_name": "TODO",
        "agency_url": "TODO",
        "agency_timezone": "Etc/UTC",
    }


def add_stop(iata_code: str, terminal: str):
    if iata_code not in stops:
        stops[iata_code] = {
            "stop_id": iata_code,
            "stop_name": f"TODO ({iata_code})",
            "stop_lat": 0.0,  # TODO
            "stop_lon": 0.0,  # TODO
            "location_type": 0,
            "stop_timezone": "Etc/UTC",  # TODO
        }
    if terminal == "" or f"{iata_code}_{terminal}" in stops:
        return
    stops[f"{iata_code}_{terminal}"] = {
        "stop_id": f"{iata_code}_{terminal}",
        "stop_name": f"TODO ({iata_code}) Terminal {terminal}",
        "stop_code": terminal,
        "stop_lat": 0.0,  # TODO
        "stop_lon": 0.0,  # TODO
        "location_type": 0,
        "parent_station": iata_code,
        "stop_timezone": "Etc/UTC",  # TODO
    }
    stops[iata_code]["location_type"] = 1


with open("af-kl-to-hv-s26-w26.ssim") as f:
    for line in f:
        if line[0] == '3':
            agency_id = line[128:130] if line[128:130].strip() != "" else line[2:4]
            add_agency(agency_id)
            from_airport = line[36:39]
            from_terminal = line[52:53].strip()
            to_airport = line[54:57]
            to_terminal = line[70:71].strip()
            add_stop(from_airport, from_terminal)
            add_stop(to_airport, to_terminal)
            route_id = line[2:8].replace(' ', '_')
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
                "trip_headsign": stops[to_airport]["stop_name"],  # TODO translations?
                "trip_short_name": line[2:8],  # TODO normalize/clean
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
    writer = csv.DictWriter(f, fieldnames=["agency_id", "agency_name", "agency_url", "agency_timezone"])
    writer.writeheader()
    for (_, agency) in agencies.items():
        writer.writerow(agency)

with open("stops.txt", 'w') as f:
    writer = csv.DictWriter(f, fieldnames=["stop_id", "stop_name", "stop_code", "stop_lat", "stop_lon", "location_type", "parent_station", "stop_timezone"])
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
