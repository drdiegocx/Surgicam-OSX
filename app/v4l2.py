"""Utilidades para consultar y ajustar controles V4L2."""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, replace
from typing import Any, Dict, List, Optional

_CONTROL_LINE_PATTERN = re.compile(
    r"^(?P<identifier>[A-Za-z0-9_]+)\s+0x[0-9a-fA-F]+\s+\((?P<type>[^)]+)\)\s*:\s*(?P<rest>.*)$"
)
_MENU_OPTION_PATTERN = re.compile(
    r"^(?P<value>-?\d+):\s*(?:\"(?P<label>[^\"]*)\"|(?P<label_plain>.*))$"
)
_KEY_VALUE_PATTERN = re.compile(r"(\w+)=([^\s]+)")

from .config import settings


class V4L2Error(RuntimeError):
    """Error específico al interactuar con v4l2-ctl."""


@dataclass
class ControlOption:
    """Representa una opción para controles de tipo menú."""

    value: int
    label: str


@dataclass
class ControlInfo:
    """Información completa de un control V4L2."""

    identifier: str
    name: str
    type: str
    value: Any
    default: Optional[Any]
    minimum: Optional[int]
    maximum: Optional[int]
    step: Optional[int]
    category: Optional[str]
    flags: Optional[List[str]]
    options: Optional[List[ControlOption]] = None

    def as_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "id": self.identifier,
            "name": self.name,
            "type": self.type,
            "value": self.value,
            "default": self.default,
            "min": self.minimum,
            "max": self.maximum,
            "step": self.step,
            "category": self.category,
            "flags": self.flags or [],
        }
        if self.options is not None:
            data["options"] = [option.__dict__ for option in self.options]
        return data


def _run_v4l2ctl(args: List[str]) -> str:
    command = ["v4l2-ctl", f"--device={settings.USTREAMER_DEVICE}", *args]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            check=True,
            text=True,
        )
    except FileNotFoundError as exc:  # pragma: no cover - entorno sin binario
        raise V4L2Error("El comando v4l2-ctl no está disponible en el sistema") from exc
    except subprocess.CalledProcessError as exc:  # pragma: no cover - errores runtime
        raise V4L2Error(exc.stderr or exc.stdout or "Fallo al ejecutar v4l2-ctl") from exc
    return result.stdout


def _parse_menu_output(raw: str) -> Dict[str, List[ControlOption]]:
    menus: Dict[str, List[ControlOption]] = {}
    current: Optional[str] = None

    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        control_match = _CONTROL_LINE_PATTERN.match(stripped)
        if control_match:
            identifier = control_match.group("identifier")
            ctrl_type = control_match.group("type").lower()
            if "menu" in ctrl_type:
                current = identifier
                menus.setdefault(identifier, [])
            else:
                current = None
            continue

        if current is None:
            continue

        option_match = _MENU_OPTION_PATTERN.match(stripped)
        if option_match:
            value = int(option_match.group("value"))
            label = option_match.group("label") or option_match.group("label_plain") or ""
            menus[current].append(ControlOption(value=value, label=label.strip()))
    return menus


def _parse_controls_json(raw: str) -> Dict[str, Dict[str, Any]]:
    try:
        payload: Dict[str, Dict[str, Any]] = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise V4L2Error("No se pudo interpretar la salida JSON de v4l2-ctl") from exc
    return payload


def _humanize_identifier(identifier: str) -> str:
    return identifier.replace("_", " ").strip().title()


def _split_flags(raw: Optional[str]) -> Optional[List[str]]:
    if not raw:
        return None
    return [flag.strip() for flag in raw.split(",") if flag.strip()]


def _coerce_numeric(raw: Optional[str]) -> Optional[int]:
    if raw is None:
        return None
    try:
        return int(raw, 0)
    except ValueError:
        return None


def _coerce_value(raw: Optional[str], ctrl_type: str) -> Any:
    if raw is None:
        return None
    lowered = ctrl_type.lower()
    if lowered in {"bool", "boolean"}:
        return raw not in {"0", "false", "no"}
    if lowered in {"menu", "intmenu", "integer_menu", "integer menu", "int", "integer", "int64"}:
        try:
            return int(raw, 0)
        except ValueError:
            return raw
    if lowered in {"float", "double"}:
        try:
            return float(raw)
        except ValueError:
            return raw
    return raw


def _parse_get_control(raw: str) -> Dict[str, str]:
    values: Dict[str, str] = {}
    for line in raw.splitlines():
        if ":" not in line:
            continue
        name, value = line.split(":", 1)
        values[name.strip()] = value.strip()
    return values


def _build_from_json(
    data: Dict[str, Dict[str, Any]], menus: Dict[str, List[ControlOption]]
) -> List[ControlInfo]:
    controls: List[ControlInfo] = []
    for identifier, control in data.items():
        ctrl_type = control.get("type", "")
        raw_flags = control.get("flags")
        if isinstance(raw_flags, list):
            flags = [str(flag) for flag in raw_flags]
        elif isinstance(raw_flags, str):
            flags = _split_flags(raw_flags)
        else:
            flags = None
        info = ControlInfo(
            identifier=identifier,
            name=control.get("name", _humanize_identifier(identifier)),
            type=ctrl_type,
            value=control.get("value"),
            default=control.get("default"),
            minimum=control.get("min"),
            maximum=control.get("max"),
            step=control.get("step"),
            category=control.get("category"),
            flags=flags,
            options=menus.get(identifier),
        )
        controls.append(info)
    return controls


def _build_from_text(raw: str, menus: Dict[str, List[ControlOption]]) -> List[ControlInfo]:
    controls: List[ControlInfo] = []
    category: Optional[str] = None

    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        match = _CONTROL_LINE_PATTERN.match(stripped)
        if match:
            identifier = match.group("identifier")
            ctrl_type = match.group("type").strip()
            attributes = match.group("rest")
            pairs = {key: value for key, value in _KEY_VALUE_PATTERN.findall(attributes)}

            control = ControlInfo(
                identifier=identifier,
                name=_humanize_identifier(identifier),
                type=ctrl_type,
                value=_coerce_value(pairs.get("value"), ctrl_type),
                default=_coerce_value(pairs.get("default"), ctrl_type),
                minimum=_coerce_numeric(pairs.get("min")),
                maximum=_coerce_numeric(pairs.get("max")),
                step=_coerce_numeric(pairs.get("step")),
                category=category,
                flags=_split_flags(pairs.get("flags")),
                options=menus.get(identifier),
            )
            controls.append(control)
            continue

        if ":" not in stripped and "(" not in stripped:
            category = stripped

    return controls


def list_controls() -> List[ControlInfo]:
    """Obtiene y normaliza la lista de controles disponibles."""

    try:
        menu_output = _run_v4l2ctl(["--list-ctrls-menus"])
    except V4L2Error:
        menus: Dict[str, List[ControlOption]] = {}
    else:
        menus = _parse_menu_output(menu_output)

    try:
        raw_controls = _run_v4l2ctl(["--list-ctrls-json"])
    except V4L2Error:
        legacy_output = _run_v4l2ctl(["--list-ctrls"])
        controls = _build_from_text(legacy_output, menus)
    else:
        data = _parse_controls_json(raw_controls)
        controls = _build_from_json(data, menus)

    controls.sort(key=lambda ctrl: ((ctrl.category or "").lower(), ctrl.name.lower()))
    return controls


def find_control(identifier: str) -> ControlInfo:
    for control in list_controls():
        if control.identifier == identifier:
            return control
    raise V4L2Error(f"El control '{identifier}' no está disponible en el dispositivo")


def _read_control_value(identifier: str, ctrl_type: str) -> Any:
    output = _run_v4l2ctl([f"--get-ctrl={identifier}"])
    values = _parse_get_control(output)
    if identifier not in values:
        raise V4L2Error(
            f"No se pudo leer el valor actualizado del control '{identifier}'"
        )
    return _coerce_value(values[identifier], ctrl_type)


def set_control(identifier: str, value: Any, template: Optional[ControlInfo] = None) -> ControlInfo:
    if isinstance(value, bool):
        value = int(value)
    value_str = str(value)
    _run_v4l2ctl([f"--set-ctrl={identifier}={value_str}"])
    if template is None:
        return find_control(identifier)
    updated_value = _read_control_value(identifier, template.type)
    return replace(template, value=updated_value)


def reset_control(identifier: str, template: Optional[ControlInfo] = None) -> ControlInfo:
    control = template or find_control(identifier)
    if control.default is None:
        raise V4L2Error(
            f"El control '{identifier}' no tiene valor predeterminado conocido"
        )
    return set_control(identifier, control.default, control)

