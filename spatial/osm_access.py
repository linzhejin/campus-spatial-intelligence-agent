"""Interpret preserved OSM access and direction tags without inventing access.

Specific mode tags override broader tags. Missing tags are unknown and retain
the highway's normal routing role. Permit-only and other limited-access tags
are excluded from public routes by default; their source values remain intact.
"""

import ast

MODE_KEYS = {"walk": ("foot", "access"), "bike": ("bicycle", "vehicle", "access"),
             "drive": ("motorcar", "motor_vehicle", "vehicle", "access")}
DRIVE_HIGHWAYS = {"motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link",
                  "secondary", "secondary_link", "tertiary", "tertiary_link", "unclassified",
                  "residential", "service", "living_street", "road", "busway"}
DENIED = {"no", "private"}
AFFIRMATIVE = {"yes", "designated", "permissive", "official"}
LIMITED = {"permit", "destination", "customers", "delivery", "agricultural", "forestry"}


def values(raw):
    if raw is None:
        return []
    if isinstance(raw, (list, tuple, set)):
        return [str(x).lower() for x in raw]
    if isinstance(raw, str) and raw.startswith("["):
        try:
            return values(ast.literal_eval(raw))
        except (ValueError, SyntaxError):
            pass
    return [str(raw).strip().lower()]


def mode_permission(tags, mode):
    for key in MODE_KEYS[mode]:
        vals = values(tags.get(key))
        if vals:
            if any(value in DENIED or value in LIMITED for value in vals):
                return False, key, ";".join(vals)
            return True, key, ";".join(vals)
    return None, None, None


def _is_forward(tags):
    value = tags.get("osm_way_forward")
    if value is None:
        # Legacy graph direction cannot be reconstructed from its oneway=False.
        return None
    return value is True or str(value).lower() in {"true", "1", "yes"}


def _direction_allowed(tags, mode):
    forward = _is_forward(tags)
    if forward is None:
        return True
    if mode == "walk":
        oneway = tags.get("oneway:foot", "no")
    else:
        oneway = tags.get("oneway:bicycle") if mode == "bike" else None
        if oneway is None:
            oneway = tags.get("oneway", "yes" if tags.get("junction") == "roundabout" else "no")
            if mode == "bike" and any("opposite" in str(tags.get(k, "")) for k in ("cycleway", "cycleway:left", "cycleway:right")):
                oneway = "no"
    vals = values(oneway)
    if any(v in {"yes", "true", "1"} for v in vals) and not forward:
        return False
    if "-1" in vals and forward:
        return False
    mode_key = {"walk": "foot", "bike": "bicycle", "drive": "motor_vehicle"}[mode]
    directional = values(tags.get(f"{mode_key}:{'forward' if forward else 'backward'}"))
    return not any(v in DENIED for v in directional)


def _advisory(tags, mode, key, value):
    items = []
    if value and value not in AFFIRMATIVE:
        if any(v in LIMITED for v in value.split(";")):
            items.append(f"{key}={value}")
        elif value == "dismount":
            items.append("bicycle=dismount")
        elif value not in DENIED:
            items.append(f"unclassified_access:{key}={value}")
    for key in MODE_KEYS[mode]:
        if tags.get(f"{key}:conditional"):
            items.append(f"{key}:conditional={tags[f'{key}:conditional']}")
    return "; ".join(items)


def edge_access(tags, mode):
    """Return (allowed, advisory) for one directed source-way segment."""
    permission, key, value = mode_permission(tags, mode)
    if permission is False:
        return False, f"{key}={value}"
    highway = set(values(tags.get("highway")))
    if highway & {"construction", "proposed", "abandoned", "razed"}:
        return False, "inactive_highway"
    if mode == "walk":
        if highway & {"motorway", "motorway_link"} and key != "foot":
            return False, "motorway_without_foot_permission"
        if str(tags.get("indoor", "")).lower() in {"yes", "room"} and key != "foot":
            return False, "indoor_route_without_foot_permission"
    elif mode == "bike":
        if highway & {"steps", "elevator", "escalator"}:
            return False, "vertical_infrastructure"
        if "corridor" in highway and key != "bicycle":
            return False, "indoor_corridor_without_bicycle_permission"
        if highway & {"motorway", "motorway_link"} and key != "bicycle":
            return False, "motorway_without_bicycle_permission"
    elif mode == "drive":
        if highway & {"steps", "elevator", "escalator", "corridor", "platform"}:
            return False, "non_motor_infrastructure"
        explicit_motor = key in {"motorcar", "motor_vehicle", "vehicle"} and permission is True
        if highway and not highway & DRIVE_HIGHWAYS and not explicit_motor:
            return False, "non_motor_highway"
    if not _direction_allowed(tags, mode):
        return False, "against_source_way_direction"
    return True, _advisory(tags, mode, key, value)


def node_access(tags, mode):
    """A gate with unknown access stays unknown; physical barriers are mode aware."""
    permission, key, value = mode_permission(tags, mode)
    if permission is False:
        return False, f"{key}={value}"
    specific = key is not None and key != "access" and permission is True
    entrances = set(values(tags.get("entrance")))
    if entrances & {"emergency", "private"} and not specific:
        return False, "restricted_entrance:" + ";".join(sorted(entrances))
    barriers = set(values(tags.get("barrier")))
    if barriers & {"wall", "fence", "hedge", "retaining_wall"} and not specific:
        return False, "physical_barrier"
    if mode == "drive" and barriers & {"bollard", "block", "stile", "turnstile", "kissing_gate", "cycle_barrier"} and not specific:
        return False, "vehicle_barrier"
    if mode == "bike" and barriers & {"stile", "turnstile", "kissing_gate"} and not specific:
        return False, "bicycle_barrier"
    return True, _advisory(tags, mode, key, value)
