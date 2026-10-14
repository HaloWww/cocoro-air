"""Read-only values decoded from Sharp's COCORO AIR web application."""

FAN_SPEED_OPTIONS = ("auto", *(f"level_{level}" for level in range(1, 9)))
OPERATION_MODES = {
    0x00: "off", 0x02: "settings", 0x0D: "humidifier_cleaning",
    0x0E: "cleaning_while_stopped", 0x0F: "error_stop", 0x10: "auto",
    0x11: "sleep", 0x13: "pollen", 0x14: "quiet", 0x15: "medium",
    0x16: "strong", 0x18: "odor_patrol", 0x1A: "turbo", 0x1B: "weak",
    0x1C: "rapid", 0x1E: "automatic_cleaning", 0x20: "ai_auto",
    0x30: "splash_particle", 0x40: "powerful_cleaning", 0x50: "powerful_shot",
    0x60: "cleaning_assist", 0x80: "demo", 0xFF: "special",
}
AI_MODES = {
    0x10: "ai_purifier_cooperation", 0x11: "ai_circulation_cooperation",
    0x20: "ai_custom", 0x81: "ai_dog_a", 0x82: "ai_dog_b",
    0x83: "ai_cat_a", 0x84: "ai_cat_b", 0x0D: "ai_humidification",
}
MODE_OPTIONS = tuple(dict.fromkeys((*OPERATION_MODES.values(), *AI_MODES.values())))
PM25_STATUS_OPTIONS = ("measured", "measuring", "below_range", "above_range", "not_measuring")

# Sensor group/field, device specification lifetime, and supplies type code.
FILTER_FIELDS = {
    "dust_filter_remaining": ("s8", "dustFilterLimit", 0x00),
    "deodorizing_filter_remaining": ("s9", "smellFilterLimit", 0x01),
    "humidifier_filter_remaining": ("s10", "humidFilterLimit", 0x02),
    "ag_ion_cartridge_remaining": ("s11", "ionUnitLimit", 0x10),
    "pci_unit_remaining": ("s4", "pciUnitLimit", 0x11),
}
