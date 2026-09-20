import ast
import importlib.util
import re
import sys
import types
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).parents[1]
MODULE_DIR = ROOT / "apps" / "pool_manager"


def _component_methods():
    owners = defaultdict(list)
    for path in MODULE_DIR.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            if not node.name.endswith(("Mixin", "Support")):
                continue
            for member in node.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    owners[member.name].append(f"{path.name}:{node.name}")
    return owners


def test_component_methods_have_one_owner_and_do_not_depend_on_mro():
    duplicates = {
        method: classes
        for method, classes in _component_methods().items()
        if len(classes) > 1
    }
    assert duplicates == {}


def test_only_appdaemon_boundary_methods_use_super_dispatch():
    offenders = []
    for path in MODULE_DIR.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            owner = node.func.value
            if isinstance(owner, ast.Call) and isinstance(owner.func, ast.Name):
                if owner.func.id == "super" and node.func.attr not in {"log", "call_service"}:
                    offenders.append(f"{path.name}:{node.lineno}:{node.func.attr}")
    assert offenders == []


def test_initialization_pipeline_is_explicit_and_unique():
    hassapi = types.ModuleType("hassapi")
    hassapi.Hass = object
    sys.modules.setdefault("hassapi", hassapi)
    sys.path.insert(0, str(MODULE_DIR))
    path = MODULE_DIR / "filtration_piscine.py"
    spec = importlib.util.spec_from_file_location("architecture_entrypoint", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.INITIALIZATION_STAGES == (
        "_initialize_runtime_stability",
        "_initialize_heating",
        "_initialize_auto_gate",
        "_initialize_safety",
        "_initialize_daylight",
        "_initialize_lifecycle",
    )
    assert len(module.INITIALIZATION_STAGES) == len(set(module.INITIALIZATION_STAGES))


V091_ENTITY_CONTRACT = {
    "entity_pool_manager_log": "sensor.pool_manager_log",
    "temperature_eau": "sensor.pool_water_temperature",
    "restitution_inst": "sensor.grid_power",
    "mode_de_fonctionnement": "input_select.pool_operating_mode",
    "entity_mode_auto": "input_boolean.pool_auto_mode",
    "h_pivot": "input_datetime.pool_pivot_time",
    "duree_filtration_ete": "input_number.pool_daily_filtration_target",
    "coef": "input_number.pool_filtration_coefficient",
    "mode_calcul": "input_boolean.pool_use_curve_calculation",
    "tempo_eau": "input_number.pool_water_circulation_delay",
    "mem_temp": "input_number.pool_water_temperature_memory",
    "arret_force": "input_boolean.pool_force_stop",
    "entity_temperature_exterieure": "sensor.outdoor_temperature",
    "entity_temperature_exterieure_moyenne_24h": "sensor.outdoor_temperature_mean_24h",
    "entity_temperature_exterieure_moyenne_7j": "sensor.outdoor_temperature_mean_7d",
    "duree_bras_hors_gel": "input_number.pool_freeze_circulation_duration",
    "intervalle_bras_hors_gel": "input_number.pool_freeze_circulation_interval",
    "cde_pompe": "fan.pool_pump",
    "fan_variateur_pompe": "fan.pool_pump",
    "entity_pompe_local_panel_assist": "switch.pool_pump_local_panel_assist",
    "entity_pompe_local_control_available": "binary_sensor.pool_pump_local_panel_control_available",
    "entity_pompe_local_control_remaining": "sensor.pool_pump_time_until_local_control",
    "message_filtration_piscine": "input_text.pool_manager_status",
    "message_filtration_piscine_detail": "input_text.pool_manager_status_detail",
    "entity_debug_w": "input_text.pool_manager_power_debug",
    "entity_pac_climate": "climate.pool_heat_pump",
    "entity_pac_conso": "sensor.pool_heat_pump_power",
    "entity_pompe_conso": "sensor.pool_pump_power",
    "entity_chauffage": "input_select.pool_heating",
    "entity_chauffage_timer": "timer.pool_heating",
    "entity_chauffage_precedent": "input_text.pool_heating_previous",
    "entity_meteo_chauffage_predictif": "weather.home",
    "entity_chauffage_predictif_status": "sensor.pool_predictive_heating",
    "entity_chauffage_predictif_score": "sensor.piscine_score_baignade",
    "entity_volume_filtre_jour": "input_number.pool_filtered_volume_today",
    "entity_temps_filtration_equivalent_jour": "input_number.pool_equivalent_filtration_today",
    "entity_date_cumul": "input_text.pool_daily_counter_date",
    "entity_volet_piscine": "cover.pool_cover",
    "entity_consigne_electrolyseur": "number.pool_chlorinator_production",
    "entity_consigne_electrolyseur_volet_ouvert": "input_number.pool_chlorination_cover_open",
    "entity_consigne_electrolyseur_volet_ferme": "input_number.pool_chlorination_cover_closed",
}


def test_canonical_home_assistant_entity_contract_is_unchanged_from_v091():
    text = (ROOT / "examples" / "filtration_piscine.yaml").read_text(encoding="utf-8")
    configured = dict(
        re.findall(r"^  ([a-z0-9_]+):\s+([^#\n]+?)\s*$", text, flags=re.MULTILINE)
    )
    assert {key: configured.get(key) for key in V091_ENTITY_CONTRACT} == V091_ENTITY_CONTRACT
