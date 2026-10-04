# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Carrier <-> technology vocabulary.

PyPSA carriers are free text; CE technology codes are an open vocabulary with
recommended codes (``spec/SPEC.md`` section 6.4). The tables below translate the carrier names
used by common open PyPSA datasets. A carrier that is not listed keeps its own
name as the technology code, and ``import_network(technology_map=...)``
extends or overrides the table.

Whether a PyPSA generator becomes an availability-bounded ``renewable`` or a
dispatchable ``thermal`` unit is decided by :data:`RENEWABLE_TECHNOLOGIES`.
"""

from __future__ import annotations

__all__ = ["TECHNOLOGY", "EXPORT_CARRIER", "RENEWABLE_TECHNOLOGIES", "ELECTRIC_BUS_CARRIERS",
           "technology_for", "carrier_for"]

#: PyPSA carrier (lower case) -> CE technology code
TECHNOLOGY: dict[str, str] = {
    # variable renewables
    "onwind": "wind_onshore", "wind": "wind_onshore", "onshore wind": "wind_onshore",
    "offwind": "wind_offshore", "offwind-ac": "wind_offshore", "offwind-dc": "wind_offshore",
    "offwind-float": "wind_offshore", "offshore wind": "wind_offshore",
    "solar": "solar_pv", "pv": "solar_pv", "solar pv": "solar_pv", "solar-hsat": "solar_pv",
    "solar rooftop": "solar_pv", "solar-utility": "solar_pv",
    "ror": "hydro_ror", "run of river": "hydro_ror", "run-of-river": "hydro_ror", "hydro": "hydro_ror",
    # dispatchable
    "ccgt": "gas_cc", "ocgt": "gas_ct", "gas": "gas_cc", "nuclear": "nuclear", "coal": "coal",
    "hard coal": "coal", "lignite": "coal", "oil": "oil", "biomass": "biomass", "solid biomass": "biomass",
    "geothermal": "geothermal",
    # storage
    "battery": "battery", "li ion": "battery", "battery storage": "battery",
    "phs": "pumped_hydro", "pumped hydro": "pumped_hydro",
    "h2": "hydrogen_storage", "hydrogen": "hydrogen_storage",
}

#: CE technology code -> carrier written on export (the first PyPSA name above for each code)
EXPORT_CARRIER: dict[str, str] = {
    "wind_onshore": "onwind", "wind_offshore": "offwind", "solar_pv": "solar", "hydro_ror": "ror",
    "gas_cc": "CCGT", "gas_ct": "OCGT", "battery": "battery", "pumped_hydro": "PHS",
    "hydrogen_storage": "H2",
}

#: technology codes imported as availability-bounded (``kind = "renewable"``) resources
RENEWABLE_TECHNOLOGIES = frozenset({"wind_onshore", "wind_offshore", "solar_pv", "hydro_ror", "hydro_reservoir"})

#: bus carriers treated as electrical nodes; buses of any other carrier are not imported
ELECTRIC_BUS_CARRIERS = frozenset({"", "ac", "dc"})


def technology_for(carrier: str, table: dict[str, str] | None = None) -> str:
    """CE technology code for a PyPSA carrier (the carrier itself when unlisted, ``"unknown"`` when blank)."""
    raw = str(carrier).strip()
    if not raw or raw.lower() == "nan":
        return "unknown"
    return (table or TECHNOLOGY).get(raw.lower(), raw)


def carrier_for(technology: str) -> str:
    """PyPSA carrier written for a CE technology code."""
    return EXPORT_CARRIER.get(technology, technology)
