"""Sharp control builders adapted from hldh214/cocoro-air b31e216.

Original implementation is MIT licensed; see the repository LICENSE.
Only supported vendor modes are exposed; no synthetic speed percentages.
"""
from __future__ import annotations
from typing import Any

MODE_CODES = {
    "ai_auto": "20", "auto": "10", "pollen": "13", "sleep": "11",
    "feel": "40", "splash": "30", "quiet": "14", "medium": "15",
    "strong": "16", "weak": "1B", "rapid": "1C", "turbo": "1A",
}

STATUS_MODES = {
    **{code: name for name, code in MODE_CODES.items()},
    "00": "off", "02": "settings", "0D": "humidifier_cleaning",
    "0E": "cleaning_stopped", "0F": "error", "18": "odor_patrol",
    "1E": "cleaning", "50": "powerful_shot", "60": "cleaning_assist",
    "80": "demo", "FF": "special",
}

AI_SUBMODES = {
    "81": "dog", "82": "dog", "83": "cat", "84": "cat",
    "0D": "ai_humidity_measurement", "20": "custom",
    "10": "purification_cooperation", "11": "circulation_cooperation",
}

def _mode_category(spec: dict) -> int:
    if spec.get("hasCustomDriveMode"):
        return 3
    if not spec.get("hasCloudService") or not spec.get("hasHumidFunc") or spec.get("cannotControlHumid"):
        return 0 if spec.get("hasCloudService") else 1
    return 2

def supported_modes(spec: dict, pets: list | None = None, status: dict | None = None) -> list[str]:
    """Modes visible in the official mode dialog, including registered pets.

    Optional ``model_name`` in spec enables Sharp's model-specific exclusions.
    Pet records use the official ``{'0x00': {'0x02': '0x00'/'0x01'}}`` shape.
    """
    model = spec.get("model_name", "")
    category = _mode_category(spec)
    modes = []
    if not spec.get("nonSpecialMode") and model != "KIM851":
        modes.append("ai_auto")
    if category != 0 or model == "KIM851":
        modes.append("auto")
    if model not in ("KIM851", "FU90KK", "FUM1200") or spec.get("hasDriveModeEco"):
        modes.extend(("pollen", "sleep"))
    if model != "KIM851" and category not in (0, 3):
        modes.append("feel")
    if spec.get("hasSplashParticle"):
        modes.append("splash")
    modes.extend(("quiet", "medium", "strong"))
    if model == "FUM1200":
        modes.extend(("weak", "rapid"))
    if spec.get("hasWeakTurbo"):
        modes.append("turbo")
    if spec.get("hasPetMode") and (status or {}).get("cloud_enabled"):
        types = {pet.get("0x00", {}).get("0x02") for pet in pets or [] if isinstance(pet, dict)}
        if "0x00" in types:
            modes.append("dog")
        if "0x01" in types:
            modes.extend(("cat", "cat_child_lock"))
    return modes

def build_command(action: str, value: Any, status: dict, spec: dict) -> list[dict]:
    """Construct an official request payload; reject unsupported capabilities."""
    current = status.get("current_mode")
    disabled = {"off", "settings", "demo", "humidifier_cleaning",
                "cleaning_stopped", "cleaning", "error"}
    if action == "power" and current in ("settings", "demo", "error"):
        raise ValueError("Power control is disabled in the current device state")
    if action == "mode" and current in disabled:
        raise ValueError("Mode control is disabled in the current device state")
    if action == "humidification" and (
        current in disabled or current == "cleaning_assist" or
        current == "feel" and not spec.get("hasHumidWhileFeel")
    ):
        raise ValueError("Humidification control is disabled in the current device state")
    if action in ("power", "humidification", "cloud") and not isinstance(value, bool):
        raise ValueError(f"{action} requires a boolean")
    if action == "power":
        return [{"epc": "0x80", "edt": "0x30" if value else "0x31"},
                {"opc": "k3", "odt": {"s6": "FF" if value else "00"}}]
    if action == "humidification":
        if not spec.get("hasHumidFunc") or spec.get("cannotControlHumid"):
            raise ValueError("Humidification control is not supported")
        return [{"opc": "k3", "odt": {"s5": "00", "s7": "FF" if value else "00"}}]
    if action == "cloud":
        if not spec.get("hasCloudService"):
            raise ValueError("Cloud service is not supported")
        return [{"opc": "k3", "odt": {"s11": "0008" if value else "0009"}}]
    if action != "mode":
        raise ValueError(f"Unknown control action: {action}")
    if value in ("dog", "cat", "cat_child_lock"):
        if not spec.get("hasPetMode") or not status.get("cloud_enabled"):
            raise ValueError("Pet modes require cloud service")
        return [{"opc": "k3", "odt": {"s11": {"dog": "0001", "cat": "0002", "cat_child_lock": "0003"}[value]}}]
    if value not in supported_modes(spec):
        raise ValueError(f"Unsupported mode: {value}")
    if value == "ai_auto":
        current = status.get("current_mode")
        if current in ("dog", "cat", "cat_child_lock"):
            odt = {"s11": "0004"}
        elif current == "ai_humidity_measurement":
            odt = {"s11": "0005"}
        else:
            odt = {"s1": "20"}
            if status.get("cloud_enabled") and spec.get("hasCustomDriveMode"):
                odt["s2"] = "80"
    else:
        odt = {"s1": MODE_CODES[value], "s5": "00"}
    return [{"opc": "k3", "odt": odt}]
