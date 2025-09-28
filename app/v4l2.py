"""Utilidades para consultar y ajustar controles V4L2."""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

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
    header_pattern = re.compile(r"^([\w\-]+)\s+\((?:menu|intmenu|integer menu)\)$", re.IGNORECASE)
    option_pattern = re.compile(r"^(\d+):\s*(?:\"([^\"]*)\"|(.*))$")

    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        header_match = header_pattern.match(stripped)
        if header_match:
            current = header_match.group(1)
            menus[current] = []
            continue
        if current is None:
            continue
        option_match = option_pattern.match(stripped)
        if option_match:
            value = int(option_match.group(1))
            label = option_match.group(2) or option_match.group(3) or ""
            menus[current].append(ControlOption(value=value, label=label))
    return menus


def _parse_controls_json(raw: str) -> Dict[str, Dict[str, Any]]:
    try:
        payload: Dict[str, Dict[str, Any]] = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise V4L2Error("No se pudo interpretar la salida JSON de v4l2-ctl") from exc
    return payload


def list_controls() -> List[ControlInfo]:
    """Obtiene y normaliza la lista de controles disponibles."""

    raw_controls = _run_v4l2ctl(["--list-ctrls-json"])
    data = _parse_controls_json(raw_controls)
    menu_output = _run_v4l2ctl(["--list-ctrls-menus"])
    menus = _parse_menu_output(menu_output)

    controls: List[ControlInfo] = []
    for identifier, control in data.items():
        options: Optional[List[ControlOption]] = None
        ctrl_type = control.get("type", "")
        if ctrl_type.lower() in {"menu", "intmenu", "integer_menu", "integer menu"}:
            options = menus.get(identifier)
        info = ControlInfo(
            identifier=identifier,
            name=control.get("name", identifier.replace("_", " ").title()),
            type=ctrl_type,
            value=control.get("value"),
            default=control.get("default"),
            minimum=control.get("min"),
            maximum=control.get("max"),
            step=control.get("step"),
            category=control.get("category"),
            flags=control.get("flags"),
            options=options,
        )
        controls.append(info)

    controls.sort(key=lambda ctrl: ((ctrl.category or "").lower(), ctrl.name.lower()))
    return controls


def find_control(identifier: str) -> ControlInfo:
    for control in list_controls():
        if control.identifier == identifier:
            return control
    raise V4L2Error(f"El control '{identifier}' no está disponible en el dispositivo")


def set_control(identifier: str, value: Any) -> ControlInfo:
    if isinstance(value, bool):
        value = int(value)
    value_str = str(value)
    _run_v4l2ctl([f"--set-ctrl={identifier}={value_str}"])
    return find_control(identifier)


def reset_control(identifier: str) -> ControlInfo:
    control = find_control(identifier)
    if control.default is None:
        raise V4L2Error(
            f"El control '{identifier}' no tiene valor predeterminado conocido"
        )
    return set_control(identifier, control.default)
