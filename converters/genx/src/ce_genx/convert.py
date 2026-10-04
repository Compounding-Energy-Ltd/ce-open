# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Map a :class:`~ce_genx.genx.GenXCase` onto the tables of a CE bundle.

The target is the CE data format 1.0 bundle (see ``spec/`` at the repository
root): ``bundle.toml`` plus ``buses``, ``hvdc`` or ``lines``, ``generators``,
``storage``, ``candidates``, ``demand`` and ``availability`` tables.

==========================  ===============================================
GenX                        CE bundle
==========================  ===============================================
zone ``n``                  ``buses`` row ``id = n``, ``name = zone = "z<n>"``
``Network.csv`` line        ``hvdc`` corridor (transport model, default) or
                            ``lines`` row (DC power flow, ``network="dc"``)
existing capacity           ``generators`` (thermal, VRE, hydro, must-run) or
                            ``storage`` row named after the GenX resource
new-build option            ``candidates`` row ``<resource>_new``
``Generators_variability``  ``availability`` (renewable generators/candidates)
``Demand_data`` / ``Load``  ``demand``
representative periods      ``[[time.blocks]]``, one per period, weighted by
                            ``Sub_Weights``
``Voll``                    ``system.voll_per_mwh``
capacity reserve margin     ``system.planning_reserve_margin`` + resource
                            ``firm_capacity_frac``
mass-based CO2 cap          ``[[x_policy]]`` emission cap in ``bundle.toml``
==========================  ===============================================

Every mapping that is not exact is recorded in the import report under a code
from :data:`NOTES`; the README explains each one.
"""

from __future__ import annotations

import datetime as _dt
import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import __version__
from .genx import GenXCase
from .report import ImportReport

__all__ = ["ImportOptions", "CEBundle", "convert", "NOTES", "FORMAT_VERSION"]

#: the CE data format version this importer writes
FORMAT_VERSION = "1.0"

#: per-unit power base used to turn line reactance in ohms into a susceptance
S_BASE_MVA = 100.0

MILE_KM = 1.609344

#: every note the importer can emit: code -> (level, message template)
NOTES: dict[str, tuple[str, str]] = {
    # -- assumptions and faithful re-expressions
    "settings_not_found": ("info", "no genx_settings.yml found; network expansion and line losses are taken as "
                           "enabled and every policy file present as active"),
    "transport_as_hvdc": ("info", "GenX's transport (pipe-flow) lines are written as controllable hvdc corridors; "
                          "line losses become converter_efficiency = 1 - loss"),
    "network_expansion_disabled": ("info", "NetworkExpansion = 0: line reinforcement options are not written"),
    "line_losses_disabled": ("info", "Trans_Loss_Segments = 0: line losses are not written"),
    "line_loss_read_as_percent": ("info", "Line_Loss_Percentage above 1 was read as a percentage and divided by 100"),
    "line_resistance_unused": ("info", "Line_Resistance_Ohms is not used by the DC power flow"),
    "long_duration_storage": ("info", "GenX links the state of charge of these LDS resources across representative "
                              "periods; the bundle has no per-unit flag for it (they are listed under "
                              "x_genx.long_duration_storage; choose inter-period storage linking when running)"),
    "ccs_netted": ("info", "CO2 capture is netted into co2_t_per_mwh and the CO2 disposal cost into "
                   "varcost_per_mwh"),
    "vre_bins_flattened": ("info", "GenX VRE bins are written as separate resources"),
    "storage_energy_fom_folded": ("info", "Fixed_OM_Cost_per_MWhyr is added to capex_per_mwh_yr (the bundle has no "
                                  "per-MWh fixed O&M column)"),
    "dollar_year_assumed": ("info", "GenX does not record the money year; it is taken as the investment year"),
    "full_year_as_representative": ("info", "the full-year case is written as one representative block weighted "
                                    "8760 h; pass start= to mark it chronological from a given instant"),
    "no_reserve_margin": ("info", "the GenX case has no active capacity reserve margin and the bundle sets none; "
                          "run without a planning-reserve constraint to match GenX"),
    "reserve_margin_disabled": ("info", "Capacity_reserve_margin.csv is present but disabled in the settings"),
    "co2_cap_disabled": ("info", "CO2_cap.csv is present but CO2Cap = 0 in the settings"),
    # -- mapped approximately
    "unbounded_build_capped": ("lossy", "GenX places no limit on new capacity for these resources "
                               "(Max_Cap_MW = -1); the bundle needs one and uses {mw:g} MW"),
    "unit_cluster_aggregated": ("lossy", "GenX commits these clusters in whole units of Cap_Size; a bundle row is "
                                "one aggregate unit, so minimum output and start costs apply to the whole row "
                                "(startup_cost = per-MW start cost x existing_mw)"),
    "ramp_symmetric": ("lossy", "ramp-up and ramp-down limits differ; the bundle has one ramp limit and uses the "
                       "tighter"),
    "fuel_price_time_averaged": ("lossy", "time-varying fuel prices are averaged, weighted by the hours each "
                                 "timestep represents, into one variable cost per resource"),
    "availability_clipped": ("lossy", "profile values outside [0, 1] were clipped; for hydro, inflow above turbine "
                             "capacity is energy GenX could store (share of energy clipped in brackets)"),
    "hydro_reservoir_as_availability": ("lossy", "GenX reservoir hydro is written as a renewable generator bounded "
                                        "by its normalised inflow: the reservoir cannot shift energy between "
                                        "periods (Hydro_Energy_to_Power_Ratio, Min_Power and ramps are not carried)"),
    "must_run_as_renewable": ("lossy", "GenX must-run output is exactly capacity x profile; as a renewable "
                              "generator it is bounded by the profile and may be curtailed"),
    "storage_charge_cost_folded": ("lossy", "the cost per MWh charged (Var_OM_Cost_per_MWh_In) is folded into the "
                                   "cost per MWh discharged as Var_OM_In / (eta_charge x eta_discharge)"),
    "asymmetric_charge_capacity": ("lossy", "GenX sizes the charge capacity of these storage resources separately "
                                   "(asymmetric storage); the bundle gives storage one power rating, so charge "
                                   "capacity equals discharge capacity and the charge-capacity costs are added "
                                   "to the power costs"),
    "storage_energy_assumed": ("lossy", "existing storage without Existing_Cap_MWh: energy capacity is taken as "
                               "Min_Duration (or 1 h) x power"),
    "reserve_margin_system_wide": ("lossy", "GenX has {n} zonal capacity reserve constraint(s) with differing "
                                   "margins or zone sets; the bundle carries one system-wide margin, the largest "
                                   "({prm:g})"),
    "reserve_derating_first_only": ("lossy", "resources carry deratings for several reserve constraints; "
                                    "firm_capacity_frac uses the first (CapRes_1 / Derating_factor_1)"),
    "line_reinforcement_lumpy": ("lossy", "GenX reinforces lines continuously; a bundle line candidate is one "
                                 "all-or-nothing upgrade of delta_rating_mw at capex_per_yr = cost per MW-yr x "
                                 "delta_rating_mw"),
    # -- not carried
    "thermal_availability_dropped": ("dropped", "GenX limits these thermal resources by a time-varying "
                                     "availability; the bundle has no thermal availability table, so they are "
                                     "available at full capacity"),
    "candidate_operating_limits_dropped": ("dropped", "the bundle's candidates table has no minimum-output, ramp "
                                           "or commitment columns; new thermal capacity is written without "
                                           "GenX's operating limits"),
    "candidate_storage_efficiency_dropped": ("dropped", "the bundle's candidates table has no efficiency or "
                                             "self-discharge columns; new storage takes the consumer's defaults "
                                             "instead of GenX's Eff_Up / Eff_Down / Self_Disch"),
    "storage_duration_bounds_dropped": ("dropped", "GenX bounds the energy/power ratio of new storage "
                                        "(Min_Duration, Max_Duration); the bundle sizes energy independently up to "
                                        "max_mwh without a ratio bound (an unbounded Max_Cap_MWh becomes max_mw x "
                                        "Max_Duration)"),
    "storage_retirement_dropped": ("dropped", "the bundle's storage table has no retirement flag"),
    "storage_existing_fom_dropped": ("dropped", "the bundle's storage table has no fixed O&M column; the FOM of "
                                     "existing storage (a sunk cost while it cannot retire) is not carried"),
    "min_capacity_dropped": ("dropped", "minimum installed capacity (Min_Cap_MW) is not carried"),
    "operating_reserves_dropped": ("dropped", "regulation and spinning-reserve parameters (Reg_*, Rsv_*) are not "
                                   "carried"),
    "fuel_options_dropped": ("dropped", "multi-fuel and piecewise fuel-use inputs are not carried; the primary Fuel "
                             "and Heat_Rate_MMBTU_per_MWh are used"),
    "resource_kind_dropped": ("dropped", "these GenX resource types (flexible demand, co-located VRE-storage, "
                              "electrolyzers, ...) have no CE bundle equivalent and are not imported"),
    "line_reserve_terms_dropped": ("dropped", "reserve-margin transfer terms on lines (DerateCapRes_*, "
                                   "CapRes_Excl_*) are not carried"),
    "angle_limits_dropped": ("dropped", "voltage-angle limits (Angle_Limit_Rad) are not carried"),
    "co2_cap_as_extension": ("dropped", "GenX mass-based CO2 caps have no core bundle field; they are recorded "
                             "as [[x_policy]] emission caps in bundle.toml (kind = emission_cap_t, value in tCO2 "
                             "per year, zones when not system-wide) — the shape the PyPSA converter reads — for a "
                             "consumer that applies them"),
    "co2_rate_cap_dropped": ("dropped", "GenX CO2Cap = {mode} is a rate-based CO2 cap (per MWh of demand or "
                             "generation); it has no bundle equivalent and is not imported"),
    "energy_share_requirement_dropped": ("dropped", "GenX energy share requirements (ESR) are not imported"),
    "minimum_capacity_requirement_dropped": ("dropped", "GenX minimum capacity requirements are not imported"),
    "maximum_capacity_requirement_dropped": ("dropped", "GenX maximum capacity requirements are not imported"),
    "demand_curtailment_segments_dropped": ("dropped", "GenX prices {n} demand-curtailment segments; the bundle "
                                            "has one value of lost load (Voll, the first segment)"),
}


@dataclass
class ImportOptions:
    """Choices the GenX inputs cannot answer on their own.

    Attributes
    ----------
    investment_year
        The bundle's single investment year (a single-stage GenX case models
        one year).
    currency, dollar_year
        The denomination of the case's money. GenX does not record it (its
        documentation uses US dollars). ``dollar_year`` defaults to the
        investment year.
    network
        ``"transport"`` (default) writes each GenX line as a controllable
        ``hvdc`` corridor, which is exactly GenX's default transport (pipe)
        flow model. ``"dc"`` writes ``lines`` with a susceptance computed from
        ``Line_Voltage_kV`` and ``Line_Reactance_Ohms``, matching GenX's DC-OPF
        option.
    unbounded_cap_mw
        Build limit written for resources GenX leaves unbounded
        (``Max_Cap_MW = -1``); the bundle needs a finite limit. Default: twice
        the system's coincident peak demand.
    start
        Start instant of a full-year (non-reduced) case. When given, the single
        block is written as chronological from this instant; otherwise as a
        representative block weighted 8760 h.
    technologies
        ``{resource name: technology code}`` overrides for the keyword-based
        technology classification.
    name, description, created_utc
        Bundle identity (defaults: the case folder name, a generated
        description and the current time).
    """

    investment_year: int = 2030
    currency: str = "USD"
    dollar_year: int | None = None
    network: str = "transport"
    unbounded_cap_mw: float | None = None
    start: _dt.datetime | None = None
    technologies: dict = field(default_factory=dict)
    name: str | None = None
    description: str | None = None
    created_utc: _dt.datetime | None = None


@dataclass
class CEBundle:
    """An in-memory CE bundle: the ``bundle.toml`` descriptor, one DataFrame per table, and the report."""

    descriptor: dict
    tables: dict
    report: ImportReport


# ---------------------------------------------------------------------------
# row helpers


def _num(row, col: str, default: float = 0.0) -> float:
    """A numeric cell, or ``default`` when the column is absent or the cell empty."""
    if col not in row.index:
        return default
    try:
        v = float(row[col])
    except (TypeError, ValueError):
        return default
    return default if math.isnan(v) else v


def _text(row, col: str) -> str:
    if col not in row.index or pd.isna(row[col]):
        return ""
    return str(row[col]).strip()


def _clip01(v: float) -> float:
    return min(max(v, 0.0), 1.0)


def _long(matrix: np.ndarray, keys: list, key_col: str, value_col: str, timesteps: int) -> pd.DataFrame:
    """Turn a ``T x n`` matrix into the long ``(key, scenario, block, period, value)`` form."""
    T, n = matrix.shape
    t = np.arange(T)
    return pd.DataFrame(
        {
            key_col: np.repeat(np.asarray(keys, dtype=object), T),
            "scenario": np.ones(T * n, dtype=np.int16),
            "block": np.tile(t // timesteps + 1, n).astype(np.int16),
            "period": np.tile(t % timesteps + 1, n).astype(np.int32),
            value_col: matrix.T.reshape(-1),
        }
    )


# ---------------------------------------------------------------------------
# technology codes


# Codes are the recommended ones of the CE data format (spec/SPEC.md section 6.4), the same codes the PyPSA
# converter emits: a bundle chained GenX -> CE -> PyPSA keeps one vocabulary.
_KEYWORDS = {
    "thermal": (
        ("nuclear", "nuclear"), ("lignite", "coal"), ("coal", "coal"), ("biomass", "biomass"),
        ("geothermal", "geothermal"), ("hydrogen", "hydrogen_ct"), ("oil", "oil"), ("combined_cycle", "gas_cc"),
        ("ccgt", "gas_cc"), ("combustion_turbine", "gas_ct"), ("ocgt", "gas_ct"), ("peaker", "gas_ct"),
        ("gas_steam", "gas_steam"), ("steam_turbine", "gas_steam"), ("natural_gas", "gas_cc"), ("gas", "gas_cc"),
    ),
    "vre": (
        ("offshore", "wind_offshore"), ("wind", "wind_onshore"), ("solar", "solar_pv"), ("photovoltaic", "solar_pv"),
        ("pv", "solar_pv"),
    ),
    "storage": (
        ("pumped", "pumped_hydro"), ("phs", "pumped_hydro"), ("battery", "battery"), ("li_ion", "battery"),
        ("lithium", "battery"), ("hydrogen", "hydrogen_storage"), ("compressed_air", "compressed_air"),
        ("caes", "compressed_air"),
    ),
}
_DEFAULT_TECH = {"thermal": "thermal", "storage": "battery", "vre": None}


def technology(row, kind: str, overrides: dict) -> str:
    """The technology code for a resource: an override, else keywords in its name or type columns."""
    name = row["resource"]
    if name in overrides:
        return str(overrides[name])
    hint = " ".join(_text(row, c) for c in ("technology", "resource_type", "resource"))
    hint = hint.lower().replace("-", "_").replace(" ", "_")
    if kind == "hydro":
        return "hydro_reservoir"
    if kind == "must_run":
        if "hydro" in hint:
            return "hydro_ror"
        return next((code for key, code in _KEYWORDS["thermal"] if key in hint), "must_run")
    for key, code in _KEYWORDS.get(kind, ()):
        if key in hint:
            return code
    code = _DEFAULT_TECH.get(kind, kind)
    if code is None:
        raise ValueError(
            f"cannot tell the technology of VRE resource {name!r} from its name; pass "
            f"technologies={{{name!r}: 'solar_pv' | 'wind_onshore' | 'wind_offshore' | ...}}"
        )
    return code


# ---------------------------------------------------------------------------
# the conversion


class _Conversion:
    """One conversion run: shared state plus one method per part of the bundle."""

    def __init__(self, case: GenXCase, opt: ImportOptions):
        if opt.network not in ("transport", "dc"):
            raise ValueError("network must be 'transport' or 'dc'")
        self.case, self.opt = case, opt
        self.rep = ImportReport()
        self.weights = case.time.hour_weights()
        self.zones = case.zones
        self.zone_name = {z: f"z{z}" for z in self.zones}
        self.demand = case.demand.reindex(columns=self.zones, fill_value=0.0)
        peak = float(self.demand.sum(axis=1).max()) if len(self.demand) else 0.0
        self.unbounded = opt.unbounded_cap_mw if opt.unbounded_cap_mw is not None else max(2.0 * peak, 1.0)

        self.settings_known = bool(case.settings)
        if not self.settings_known:
            self.note("settings_not_found")
        self.ucommit = int(case.setting("ucommit", 0) or 0)

        self.gens: list[dict] = []
        self.stores: list[dict] = []
        self.cands: list[dict] = []
        self.avail: dict[str, np.ndarray] = {}
        self.varying_fuels: set[str] = set()
        self.prm = self._reserve_margin()

    # -- helpers

    def note(self, code: str, items=None, **fmt) -> None:
        level, message = NOTES[code]
        self.rep.add(code, level, message.format(**fmt), items)

    def policy_active(self, key: str, setting: str) -> bool:
        """A GenX policy applies when its file exists and its setting is on (or no settings were found)."""
        if key not in self.case.policies:
            return False
        return not self.settings_known or int(self.case.setting(setting, 0) or 0) > 0

    def firm(self, row) -> float | None:
        return None if self.prm is None else _clip01(_num(row, "capres_1"))

    def mean(self, series: np.ndarray) -> float:
        """Average over the year, each timestep weighted by the hours it represents."""
        return float(np.dot(series, self.weights) / self.weights.sum())

    def fuel(self, row) -> tuple[float, float]:
        """(time-averaged price, tCO2 per MMBtu) of a resource's fuel."""
        name = _text(row, "fuel")
        if name == "" or name.lower() == "none":
            return 0.0, 0.0
        cols = {str(c).lower(): c for c in self.case.fuel_prices.columns}
        col = name if name in self.case.fuel_prices.columns else cols.get(name.lower())
        if col is None:
            raise ValueError(f"resource {row['resource']!r} uses fuel {name!r}, which Fuels_data.csv does not list")
        prices = self.case.fuel_prices[col].to_numpy(dtype=float)
        if np.ptp(prices) > 1e-9:
            self.varying_fuels.add(str(col))
        return self.mean(prices), float(self.case.fuel_co2.get(col, 0.0))

    def profile(self, name: str, required: bool) -> np.ndarray | None:
        var = self.case.variability
        col = name if name in var.columns else {str(c).lower(): c for c in var.columns}.get(name.lower())
        if col is None:
            if required:
                raise ValueError(f"Generators_variability.csv has no column for resource {name!r}")
            return None
        return var[col].to_numpy(dtype=float)

    def build_limit(self, row, existing: float) -> float:
        """MW of new capacity the resource may add (0 when it cannot be built)."""
        if _num(row, "new_build") <= 0:
            return 0.0
        cap = _num(row, "max_cap_mw", -1.0)
        if cap < 0:
            self.note("unbounded_build_capped", [row["resource"]], mw=self.unbounded)
            return self.unbounded
        return max(cap - existing, 0.0)

    # -- policies that shape the resource rows

    def _reserve_margin(self) -> float | None:
        crm = self.case.policies.get("capacity_reserve_margin")
        if crm is not None and not self.policy_active("capacity_reserve_margin", "capacityreservemargin"):
            self.note("reserve_margin_disabled")
            crm = None
        prm = None
        if crm is not None:
            cols = sorted(c for c in crm.columns if c.startswith("capres_"))
            values = crm[cols].to_numpy(dtype=float) if cols else np.zeros((0, 0))
            if (values > 0).any():
                prm = float(values[values > 0].max())
                uniform = len(cols) == 1 and set(crm["zone"]) >= set(self.zones) and np.allclose(values, prm)
                if not uniform:
                    self.note("reserve_margin_system_wide", n=len(cols), prm=prm)
            if len([c for c in self.case.resources.columns if c.startswith("capres_")]) > 1:
                self.note("reserve_derating_first_only")
        if prm is None:
            self.note("no_reserve_margin")
        return prm

    # -- resources

    def resource(self, row) -> None:
        name = row["resource"]
        if _num(row, "min_cap_mw") > 0:
            self.note("min_capacity_dropped", [name])
        if _num(row, "reg_max") > 0 or _num(row, "rsv_max") > 0:
            self.note("operating_reserves_dropped", [name])
        kind = row["kind"]
        if kind == "thermal":
            self.thermal(row)
        elif kind in ("vre", "hydro", "must_run"):
            self.renewable(row)
        elif kind == "storage":
            self.storage(row)
        else:
            self.note("resource_kind_dropped", [f"{name} ({kind})"])

    def thermal(self, row) -> None:
        name, bus = row["resource"], int(row["zone"])
        existing = max(_num(row, "existing_cap_mw"), 0.0)
        new_mw = self.build_limit(row, existing)
        tech = technology(row, "thermal", self.opt.technologies)

        price, co2_per_mmbtu = self.fuel(row)
        heat_rate = _num(row, "heat_rate_mmbtu_per_mwh")
        capture = _clip01(_num(row, "co2_capture_fraction"))
        gross = heat_rate * co2_per_mmbtu
        varcost = (_num(row, "var_om_cost_per_mwh") + heat_rate * price
                   + gross * capture * _num(row, "ccs_disposal_cost_per_metric_ton"))
        co2 = gross * (1.0 - capture)
        if capture > 0:
            self.note("ccs_netted", [name])

        commit = int(_num(row, "model")) == 1  # Model/THERM 1: unit commitment; 2: no commitment
        up, down = _num(row, "ramp_up_percentage", 1.0), _num(row, "ramp_dn_percentage", 1.0)
        if abs(up - down) > 1e-9:
            self.note("ramp_symmetric", [name])
        fom = _num(row, "fixed_om_cost_per_mwyr")
        cap_size = _num(row, "cap_size")

        if existing > 0:
            start_per_mw = _num(row, "start_cost_per_mw") + _num(row, "start_fuel_mmbtu_per_mw") * price
            if commit and 0 < cap_size < existing - 1e-9:
                self.note("unit_cluster_aggregated", [name])
            self.gens.append(dict(
                name=name, bus=bus, technology=tech, kind="thermal", existing_mw=existing,
                varcost_per_mwh=varcost, fom_per_mw_yr=fom,
                pmin_frac=_clip01(_num(row, "min_power")), ramp_frac_per_h=max(min(up, down), 0.0),
                min_up_h=int(round(_num(row, "up_time"))) if commit else 0,
                min_down_h=int(round(_num(row, "down_time"))) if commit else 0,
                startup_cost=start_per_mw * existing if commit else 0.0, noload_cost_per_h=0.0,
                co2_t_per_mwh=co2, firm_capacity_frac=self.firm(row), retirable=_num(row, "can_retire") > 0,
            ))
            prof = self.profile(name, required=False)
            if prof is not None and np.any(prof < 1.0 - 1e-9):
                self.note("thermal_availability_dropped", [name])

        if new_mw > 0:
            cand = dict(
                candidate_id=f"{name}_new", technology=tech, kind="thermal", bus=bus, max_mw=new_mw,
                capex_per_mw_yr=_num(row, "inv_cost_per_mwyr"), fom_per_mw_yr=fom, varcost_per_mwh=varcost,
                co2_t_per_mwh=co2, firm_capacity_frac=self.firm(row),
            )
            # GenX's integer unit commitment (UCommit = 1) builds commit resources in whole units
            if commit and self.ucommit == 1 and cap_size > 0:
                cand.update(integer_build=True, unit_size_mw=cap_size)
            self.cands.append(cand)
            if commit or _num(row, "min_power") > 0 or min(up, down) < 1.0:
                self.note("candidate_operating_limits_dropped", [name])

    def renewable(self, row) -> None:
        """VRE, hydro and must-run: capacity bounded by a per-period availability profile."""
        name, bus, kind = row["resource"], int(row["zone"]), row["kind"]
        existing = max(_num(row, "existing_cap_mw"), 0.0)
        new_mw = self.build_limit(row, existing)
        tech = technology(row, kind, self.opt.technologies)
        prof = self.profile(name, required=True)
        if np.any(prof > 1.0 + 1e-9) or np.any(prof < -1e-9):
            clipped = self.mean(np.clip(prof - 1.0, 0.0, None)) / max(self.mean(np.clip(prof, 0.0, None)), 1e-12)
            self.note("availability_clipped", [f"{name} ({clipped:.1%})"])
        cf = np.clip(prof, 0.0, 1.0)
        if kind == "hydro":
            self.note("hydro_reservoir_as_availability", [name])
        elif kind == "must_run":
            self.note("must_run_as_renewable", [name])
        if _num(row, "num_vre_bins") > 1:
            self.note("vre_bins_flattened", [name])

        fom, vom = _num(row, "fixed_om_cost_per_mwyr"), _num(row, "var_om_cost_per_mwh")
        if existing > 0:
            self.gens.append(dict(
                name=name, bus=bus, technology=tech, kind="renewable", existing_mw=existing,
                varcost_per_mwh=vom, fom_per_mw_yr=fom, firm_capacity_frac=self.firm(row),
                retirable=_num(row, "can_retire") > 0,
            ))
            self.avail[name] = cf
        if new_mw > 0:
            cid = f"{name}_new"
            self.cands.append(dict(
                candidate_id=cid, technology=tech, kind="renewable", bus=bus, max_mw=new_mw,
                capex_per_mw_yr=_num(row, "inv_cost_per_mwyr"), fom_per_mw_yr=fom, varcost_per_mwh=vom,
                firm_capacity_frac=self.firm(row), cf_mean=self.mean(cf),
            ))
            self.avail[cid] = cf

    def storage(self, row) -> None:
        name, bus = row["resource"], int(row["zone"])
        existing = max(_num(row, "existing_cap_mw"), 0.0)
        new_mw = self.build_limit(row, existing)
        tech = technology(row, "storage", self.opt.technologies)
        eta_c, eta_d = _num(row, "eff_up", 1.0), _num(row, "eff_down", 1.0)
        self_disch = min(max(_num(row, "self_disch"), 0.0), 0.999999)
        vom_in = _num(row, "var_om_cost_per_mwh_in")
        varcost = _num(row, "var_om_cost_per_mwh")
        if vom_in > 0 and eta_c * eta_d > 0:
            varcost += vom_in / (eta_c * eta_d)
            self.note("storage_charge_cost_folded", [name])
        asymmetric = int(_num(row, "model")) == 2  # Model/STOR 2: separately sized charge capacity
        if asymmetric:
            self.note("asymmetric_charge_capacity", [name])
        if _num(row, "lds") > 0:
            self.note("long_duration_storage", [name])
        min_d, max_d = _num(row, "min_duration"), _num(row, "max_duration")
        fom, fom_mwh = _num(row, "fixed_om_cost_per_mwyr"), _num(row, "fixed_om_cost_per_mwhyr")
        e_existing = max(_num(row, "existing_cap_mwh"), 0.0)

        if existing > 0:
            if e_existing > 0:
                duration = e_existing / existing
            else:
                duration = min_d if min_d > 0 else 1.0
                self.note("storage_energy_assumed", [name])
            self.stores.append(dict(
                name=name, bus=bus, technology=tech, existing_mw=existing, duration_h=duration,
                eta_charge=eta_c, eta_discharge=eta_d, self_discharge_frac_per_h=self_disch,
                varcost_per_mwh=varcost, firm_capacity_frac=self.firm(row),
            ))
            if _num(row, "can_retire") > 0:
                self.note("storage_retirement_dropped", [name])
            if fom > 0 or fom_mwh > 0:
                self.note("storage_existing_fom_dropped", [name])

        if new_mw > 0:
            inv_mwh = _num(row, "inv_cost_per_mwhyr")
            capex_mw = _num(row, "inv_cost_per_mwyr")
            fom_mw = fom
            if asymmetric:
                capex_mw += _num(row, "inv_cost_charge_per_mwyr")
                fom_mw += _num(row, "fixed_om_cost_charge_per_mwyr")
            cand = dict(candidate_id=f"{name}_new", technology=tech, kind="storage", bus=bus, max_mw=new_mw)
            if min_d > 0 and abs(max_d - min_d) <= 1e-9:
                # fixed duration: energy is tied to power, so per-MWh costs become per-MW costs
                cand.update(duration_h=min_d, capex_per_mw_yr=capex_mw + min_d * inv_mwh,
                            fom_per_mw_yr=fom_mw + min_d * fom_mwh)
            else:
                e_cap = _num(row, "max_cap_mwh", -1.0)
                max_mwh = new_mw * (max_d if max_d > 0 else 24.0) if e_cap < 0 else max(e_cap - e_existing, 0.0)
                cand.update(max_mwh=max_mwh, capex_per_mw_yr=capex_mw, fom_per_mw_yr=fom_mw,
                            capex_per_mwh_yr=inv_mwh + fom_mwh)
                if min_d > 0 or max_d > 0:
                    self.note("storage_duration_bounds_dropped", [name])
                if fom_mwh > 0:
                    self.note("storage_energy_fom_folded", [name])
            cand.update(varcost_per_mwh=varcost, firm_capacity_frac=self.firm(row))
            self.cands.append(cand)
            if abs(eta_c - 0.95) > 1e-9 or abs(eta_d - 0.95) > 1e-9 or self_disch > 0:
                self.note("candidate_storage_efficiency_dropped", [name])

    # -- network

    def network(self) -> dict:
        net = self.case.network
        if net is None or not len(net):
            return {}
        expansion = bool(self.case.setting("networkexpansion", 1))
        losses_on = self.case.setting("trans_loss_segments", 1) != 0

        def has_positive(col):
            return col in net.columns and (net[col].fillna(0).astype(float) > 0).any()

        if not expansion and has_positive("line_max_reinforcement_mw"):
            self.note("network_expansion_disabled")
        if not losses_on and has_positive("line_loss_percentage"):
            self.note("line_losses_disabled")
        if self.prm is not None and any(c.startswith(("deratecapres_", "capres_excl_")) for c in net.columns):
            self.note("line_reserve_terms_dropped")

        rows = []
        for _, r in net.iterrows():
            lid = int(r["network_lines"])
            add = _num(r, "line_max_reinforcement_mw") if expansion else 0.0
            loss = _num(r, "line_loss_percentage") if losses_on else 0.0
            name = _text(r, "transmission_path_name") or f"line{lid}"
            if loss > 1.0:
                self.note("line_loss_read_as_percent", [name])
                loss /= 100.0
            rows.append(dict(
                id=lid, name=name, from_bus=int(r["start_zone"]), to_bus=int(r["end_zone"]),
                rating=max(_num(r, "line_max_flow_mw"), 0.0), add=self.unbounded if add < 0 else add,
                cost=_num(r, "line_reinforcement_cost_per_mwyr"), loss=min(max(loss, 0.0), 0.999),
            ))
        base = pd.DataFrame(rows)
        ends = base[["id", "name", "from_bus", "to_bus"]]

        if self.opt.network == "transport":
            self.note("transport_as_hvdc")
            return {"hvdc": ends.assign(
                existing_mw=base["rating"], candidate_max_mw=base["add"],
                converter_efficiency=1.0 - base["loss"], capex_per_mw_yr=base["cost"],
            )}

        if not {"line_voltage_kv", "line_reactance_ohms"} <= set(net.columns):
            raise ValueError("network='dc' needs Line_Voltage_kV and Line_Reactance_Ohms in Network.csv "
                             "(GenX's DC-OPF inputs)")
        kv = net["line_voltage_kv"].astype(float).to_numpy()
        x = net["line_reactance_ohms"].astype(float).to_numpy()
        if not (np.all(kv > 0) and np.all(x > 0)):
            raise ValueError("network='dc' needs positive Line_Voltage_kV and Line_Reactance_Ohms on every line")
        lines = ends.assign(
            susceptance_pu=kv**2 / (S_BASE_MVA * x),  # b = 1/x_pu, x_pu = X / (kV^2 / S_base)
            rating_mw=base["rating"], existing=True, candidate=base["add"] > 0,
            delta_rating_mw=base["add"], capex_per_yr=base["cost"] * base["add"], loss_frac=base["loss"],
        )
        if "distance_km" in net.columns:
            lines["length_km"] = net["distance_km"].astype(float).to_numpy()
        elif "distance_mile" in net.columns:
            lines["length_km"] = net["distance_mile"].astype(float).to_numpy() * MILE_KM
        if (base["add"] > 0).any():
            self.note("line_reinforcement_lumpy")
        if has_positive("angle_limit_rad"):
            self.note("angle_limits_dropped")
        if "line_resistance_ohms" in net.columns:
            self.note("line_resistance_unused")
        return {"lines": lines}

    # -- system-wide policies

    def co2_caps(self) -> list[dict]:
        df = self.case.policies.get("co2_cap")
        if df is None:
            return []
        mode = int(self.case.setting("co2cap", 1 if not self.settings_known else 0) or 0)
        if mode == 0:
            self.note("co2_cap_disabled")
            return []
        if mode != 1:
            self.note("co2_rate_cap_dropped", mode=mode)
            return []
        caps = []
        every_zone = {self.zone_name[z] for z in self.zones}
        for k in sorted(int(c.rsplit("_", 1)[1]) for c in df.columns if c.startswith("co_2_cap_zone_")):
            member = df[df[f"co_2_cap_zone_{k}"].fillna(0) > 0]
            col = f"co_2_max_mtons_{k}"
            if member.empty or col not in df.columns:
                continue
            zones = [self.zone_name.get(int(z), f"z{int(z)}") for z in member["zone"]]
            # the [[x_policy]] shape shared with the PyPSA converter (see README): no `zones` = system-wide
            cap = {"name": f"genx_co2_cap_{k}", "kind": "emission_cap_t", "pollutant": "co2",
                   "value": float(member[col].astype(float).sum() * 1e6)}  # GenX gives Mt
            if set(zones) != every_zone:
                cap["zones"] = zones
            caps.append(cap)
        if caps:
            self.note("co2_cap_as_extension", [c["name"] for c in caps])
        return caps

    def other_policies(self) -> None:
        for key, setting in (("energy_share_requirement", "energysharerequirement"),
                             ("minimum_capacity_requirement", "mincapreq"),
                             ("maximum_capacity_requirement", "maxcapreq")):
            if self.policy_active(key, setting):
                self.note(f"{key}_dropped")
        if self.case.demand_segments > 1:
            self.note("demand_curtailment_segments_dropped", n=self.case.demand_segments)
        fuel_cols = [c for c in self.case.resources.columns if c.startswith(("multi_fuels", "pwfu_"))]
        if fuel_cols:
            self.note("fuel_options_dropped", fuel_cols)

    # -- assembly

    def blocks(self) -> list[dict]:
        ts = self.case.time
        full_year = ts.rep_periods == 1 and abs(ts.weights_h[0] - ts.timesteps) < 1e-6
        out = []
        for p in range(ts.rep_periods):
            block = {"id": p + 1, "nperiods": ts.timesteps, "dt_h": 1.0, "weight_h": float(ts.weights_h[p])}
            if full_year and self.opt.start is not None:
                block.update(kind="chronological", start=self.opt.start)
            else:
                block["kind"] = "representative"
            out.append(block)
        if full_year and self.opt.start is None:
            self.note("full_year_as_representative")
        return out

    def tables(self, network: dict) -> dict:
        def frame(rows, with_id=True):
            df = pd.DataFrame(rows)
            if with_id:
                df.insert(0, "id", np.arange(1, len(df) + 1))
            if "firm_capacity_frac" in df.columns and df["firm_capacity_frac"].isna().all():
                df = df.drop(columns=["firm_capacity_frac"])  # no reserve margin: leave consumer defaults
            return df

        steps = self.case.time.timesteps
        out = {"buses": pd.DataFrame({
            "id": self.zones,
            "name": [self.zone_name[z] for z in self.zones],
            "zone": [self.zone_name[z] for z in self.zones],
        })}
        out.update(network)
        if self.gens:
            out["generators"] = frame(self.gens)
        if self.stores:
            out["storage"] = frame(self.stores)
        if self.cands:
            cands = frame(self.cands, with_id=False)
            if "integer_build" in cands.columns:
                cands["integer_build"] = cands["integer_build"].astype("boolean").fillna(False).astype(bool)
            out["candidates"] = cands
        out["demand"] = _long(self.demand.to_numpy(dtype=float), self.zones, "bus", "mw", steps)
        if self.avail:
            out["availability"] = _long(np.column_stack(list(self.avail.values())), list(self.avail),
                                        "site_name", "cf", steps)
        return out

    def descriptor(self, blocks: list[dict], co2_caps: list[dict]) -> dict:
        case, opt = self.case, self.opt
        if opt.dollar_year is None:
            self.note("dollar_year_assumed")
        system: dict = {"discount_factors": [1.0]}
        if not math.isnan(case.voll) and case.voll > 0:
            system["voll_per_mwh"] = float(case.voll)
        if self.prm is not None:
            system["planning_reserve_margin"] = self.prm
        extension: dict = {
            "importer": f"ce-genx {__version__}",
            "source_layout": case.layout,
            "network_representation": opt.network,
            "unbounded_cap_mw": float(self.unbounded),
        }
        lds = [r["resource"] for _, r in case.resources.iterrows() if r["kind"] == "storage" and _num(r, "lds") > 0]
        if lds:
            extension["long_duration_storage"] = lds
        doc = {
            "format": {"name": "ce-bundle", "version": FORMAT_VERSION},
            "bundle": {
                "name": opt.name or case.root.name,
                "description": opt.description
                or f"Imported from a GenX case ({case.layout} input layout) by ce-genx {__version__}.",
                "created_utc": opt.created_utc or _dt.datetime.now(_dt.timezone.utc),
                "source": "GenX case folder converted with ce-genx; see x_genx_import_report.toml",
            },
            "money": {"currency": opt.currency, "dollar_year": int(opt.dollar_year or opt.investment_year)},
            "time": {
                "timezone": "UTC",
                "hour_convention": "hour_ending",
                "investment_years": [int(opt.investment_year)],
                "blocks": blocks,
            },
            "scenarios": {"names": ["base"], "probability": [1.0]},
            "system": system,
            "x_genx": extension,
        }
        if co2_caps:
            doc["x_policy"] = co2_caps
        return doc

    def run(self) -> CEBundle:
        for _, row in self.case.resources.iterrows():
            self.resource(row)
        if self.varying_fuels:
            self.note("fuel_price_time_averaged", sorted(self.varying_fuels))
        clash = sorted({c["candidate_id"] for c in self.cands} & {g["name"] for g in self.gens})
        if clash:
            raise ValueError(f"candidate ids clash with generator names: {clash}")
        network = self.network()
        co2_caps = self.co2_caps()
        self.other_policies()
        blocks = self.blocks()
        tables = self.tables(network)
        descriptor = self.descriptor(blocks, co2_caps)

        ts = self.case.time
        kinds = self.case.resources["kind"].value_counts().to_dict()
        self.rep.summary.update({
            "source_format": "GenX",
            "source_layout": self.case.layout,
            "zones": len(self.zones),
            "resources": int(len(self.case.resources)),
            "timesteps": int(ts.T),
            "representative_periods": int(ts.rep_periods),
            "network": self.opt.network,
            **{f"resources_{k}": int(v) for k, v in sorted(kinds.items())},
        })
        return CEBundle(descriptor=descriptor, tables=tables, report=self.rep)


def convert(case: GenXCase, options: ImportOptions | None = None) -> CEBundle:
    """Convert a GenX case to an in-memory CE bundle (see the module docstring)."""
    return _Conversion(case, options or ImportOptions()).run()
