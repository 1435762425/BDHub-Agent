"""发送话术模板的声明校验与逐线索安全渲染。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping


SYSTEM_PLACEHOLDERS = frozenset({
    "handle",
    "creator_id",
    "oec_id",
    "pid",
    "market",
    "product_name",
    "gmv_tier",
    "source_type",
})


class TemplateValidationError(ValueError):
    """模板正文或占位符声明不符合可审计渲染契约。"""


@dataclass(frozen=True, slots=True)
class TemplateRenderResult:
    text: str | None
    missing_required: tuple[str, ...]
    value_sources: dict[str, str]


def _placeholder_name(value: object) -> str:
    name = str(value or "").strip()
    if (
        not 1 <= len(name) <= 64
        or any(character.isspace() or character in "{}" or ord(character) < 32
               for character in name)
    ):
        raise TemplateValidationError(
            f"invalid_template_placeholder:{name or '<empty>'}"
        )
    return name


def _normalize_declarations(declarations: object) -> list[dict]:
    if declarations is None:
        declarations = []
    if not isinstance(declarations, list):
        raise TemplateValidationError("invalid_template_placeholders")
    normalized: list[dict] = []
    seen: set[str] = set()
    for declaration in declarations:
        if not isinstance(declaration, dict):
            raise TemplateValidationError("invalid_template_placeholder")
        name = _placeholder_name(declaration.get("name"))
        if name in seen:
            raise TemplateValidationError(
                f"duplicate_template_placeholder:{name}"
            )
        seen.add(name)
        normalized.append({
            "name": name,
            "desc": str(declaration.get("desc") or "").strip(),
            "required": bool(declaration.get("required")),
            "default": str(declaration.get("default") or ""),
        })
    return normalized


def _tokens(body: object) -> list[tuple[str, str]]:
    text = str(body or "")
    if not text.strip():
        raise TemplateValidationError("template_body_required")
    tokens: list[tuple[str, str]] = []
    literal: list[str] = []

    def flush_literal() -> None:
        if literal:
            tokens.append(("text", "".join(literal)))
            literal.clear()

    index = 0
    while index < len(text):
        character = text[index]
        if character == "{":
            if index + 1 < len(text) and text[index + 1] == "{":
                literal.append("{")
                index += 2
                continue
            close = text.find("}", index + 1)
            if close < 0:
                raise TemplateValidationError("invalid_template_syntax")
            flush_literal()
            tokens.append((
                "placeholder",
                _placeholder_name(text[index + 1:close]),
            ))
            index = close + 1
            continue
        if character == "}":
            if index + 1 < len(text) and text[index + 1] == "}":
                literal.append("}")
                index += 2
                continue
            raise TemplateValidationError("invalid_template_syntax")
        literal.append(character)
        index += 1
    flush_literal()
    return tokens


def validate_template(body: object, declarations: object) -> list[dict]:
    """校验正文只使用已声明占位符，并返回规范化声明。"""
    normalized = _normalize_declarations(declarations)
    declared = {item["name"] for item in normalized}
    for kind, value in _tokens(body):
        if kind == "placeholder" and value not in declared:
            raise TemplateValidationError(
                f"template_placeholder_undeclared:{value}"
            )
    return normalized


def compatible_template_declarations(
    body: object,
    declarations: object,
) -> list[dict]:
    """兼容升级前没有占位符声明的旧模板，新模板仍走严格校验。"""
    try:
        return validate_template(body, declarations)
    except TemplateValidationError:
        if declarations not in (None, []):
            raise
        inferred = [
            {
                "name": name,
                "desc": "从旧模板正文自动识别",
                "required": True,
                "default": "",
            }
            for name in unresolved_placeholder_names(body)
        ]
        return validate_template(body, inferred)


def unresolved_placeholder_names(body: object) -> tuple[str, ...]:
    """返回仍以单花括号存在的占位符；双花括号按普通文本处理。"""
    return tuple(dict.fromkeys(
        value for kind, value in _tokens(body) if kind == "placeholder"
    ))


def _present(mapping: Mapping[str, object], name: str) -> str | None:
    value = mapping.get(name)
    if value is None:
        return None
    text = str(value)
    return text if text.strip() else None


def render_template(
    body: object,
    declarations: object,
    *,
    system_values: Mapping[str, object],
    import_values: Mapping[str, object],
) -> TemplateRenderResult:
    """按系统事实、导入值、默认值的优先级渲染一条线索。"""
    normalized = validate_template(body, declarations)
    by_name = {item["name"]: item for item in normalized}
    values: dict[str, str] = {}
    value_sources: dict[str, str] = {}
    missing: list[str] = []

    used_names = list(dict.fromkeys(
        value for kind, value in _tokens(body) if kind == "placeholder"
    ))
    for name in used_names:
        declaration = by_name[name]
        value = _present(system_values, name)
        source = "system"
        if value is None:
            value = _present(import_values, name)
            source = "import"
        if value is None and declaration["default"] != "":
            value = declaration["default"]
            source = "default"
        if value is None and declaration["required"]:
            missing.append(name)
            value_sources[name] = "missing"
            continue
        if value is None:
            value = ""
            source = "empty"
        values[name] = value
        value_sources[name] = source

    if missing:
        return TemplateRenderResult(
            text=None,
            missing_required=tuple(missing),
            value_sources=value_sources,
        )
    rendered = "".join(
        value if kind == "text" else values[value]
        for kind, value in _tokens(body)
    )
    return TemplateRenderResult(
        text=rendered,
        missing_required=(),
        value_sources=value_sources,
    )


def select_import_values(
    values: Mapping[str, object],
    declarations: Iterable[Mapping[str, object]],
) -> dict[str, object]:
    """仅保留模板实际声明的自定义字段，系统字段永不从导入快照取值。"""
    selected: dict[str, object] = {}
    for declaration in declarations or ():
        if not isinstance(declaration, Mapping):
            continue
        name = str(declaration.get("name") or "").strip()
        if name and name not in SYSTEM_PLACEHOLDERS and name in values:
            selected[name] = values[name]
    return selected


__all__ = [
    "SYSTEM_PLACEHOLDERS",
    "TemplateRenderResult",
    "TemplateValidationError",
    "compatible_template_declarations",
    "render_template",
    "select_import_values",
    "unresolved_placeholder_names",
    "validate_template",
]
