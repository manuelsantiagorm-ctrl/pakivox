from __future__ import annotations

from dataclasses import asdict, dataclass
import ast
from pathlib import Path
from typing import Iterable


LIFECYCLE_MODELS = frozenset(
    {
        "ShippingPurchase",
        "CarrierShipment",
        "ShipmentPackage",
        "ShippingProviderEvent",
    }
)

AUTHORIZED_FILES = frozenset(
    {
        "services/lifecycle_service.py",
    }
)

IGNORED_PARTS = frozenset(
    {
        "__pycache__",
        "migrations",
        "tests",
    }
)

ENTITY_VARIABLES = frozenset(
    {
        "purchase",
        "shipment",
        "carrier_shipment",
        "locked_shipment",
        "package",
        "locked_package",
        "event",
        "provider_event",
    }
)


@dataclass(frozen=True)
class DirectStateWrite:
    file: str
    line: int
    column: int
    kind: str
    target: str
    code: str


def _attribute_chain(node: ast.AST) -> str:
    parts = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return ".".join(reversed(parts))


def _source_segment(source: str, node: ast.AST) -> str:
    value = ast.get_source_segment(source, node) or ""
    return " ".join(value.strip().split())[:500]


def _references_lifecycle_status(node: ast.AST | None) -> bool:
    if node is None:
        return False
    for item in ast.walk(node):
        if not isinstance(item, ast.Attribute):
            continue
        chain = _attribute_chain(item)
        if any(
            chain.startswith(f"{model}.STATUS_")
            for model in LIFECYCLE_MODELS
        ):
            return True
    return False


def _target_is_lifecycle_candidate(target: ast.AST) -> tuple[bool, str]:
    if not isinstance(target, ast.Attribute) or target.attr != "status":
        return False, ""

    owner = target.value
    chain = _attribute_chain(target)
    if isinstance(owner, ast.Name) and owner.id in ENTITY_VARIABLES:
        return True, chain

    if any(
        token in chain.casefold()
        for token in (
            "shipment",
            "package",
            "purchase",
            "provider_event",
        )
    ):
        return True, chain

    return False, chain


def _query_receiver_is_lifecycle_candidate(
    node: ast.Call,
) -> tuple[bool, str]:
    if not isinstance(node.func, ast.Attribute):
        return False, ""
    if node.func.attr != "update":
        return False, ""

    chains = [
        _attribute_chain(item)
        for item in ast.walk(node.func.value)
        if isinstance(item, ast.Attribute)
    ]
    chains = [chain for chain in chains if chain]
    receiver = max(chains, key=len, default="")
    normalized = " ".join(chains).casefold()

    if any(
        token in normalized
        for token in (
            "carriershipment.objects",
            "shipmentpackage.objects",
            "shippingpurchase.objects",
            "shippingproviderevent.objects",
            ".packages",
        )
    ):
        return True, receiver
    return False, receiver


class DirectStateWriteVisitor(ast.NodeVisitor):
    def __init__(self, *, source: str, relative_file: str):
        self.source = source
        self.relative_file = relative_file
        self.findings: list[DirectStateWrite] = []

    def _record(self, node: ast.AST, *, kind: str, target: str):
        self.findings.append(
            DirectStateWrite(
                file=self.relative_file,
                line=int(getattr(node, "lineno", 0) or 0),
                column=int(getattr(node, "col_offset", 0) or 0),
                kind=kind,
                target=target,
                code=_source_segment(self.source, node),
            )
        )

    def visit_Assign(self, node: ast.Assign):
        for target in node.targets:
            candidate, chain = _target_is_lifecycle_candidate(target)
            if candidate and (
                _references_lifecycle_status(node.value)
                or chain.split(".", 1)[0] in ENTITY_VARIABLES
            ):
                self._record(
                    node,
                    kind="attribute_assignment",
                    target=chain,
                )
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign):
        candidate, chain = _target_is_lifecycle_candidate(node.target)
        if candidate and (
            _references_lifecycle_status(node.value)
            or chain.split(".", 1)[0] in ENTITY_VARIABLES
        ):
            self._record(
                node,
                kind="annotated_assignment",
                target=chain,
            )
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign):
        candidate, chain = _target_is_lifecycle_candidate(node.target)
        if candidate:
            self._record(
                node,
                kind="augmented_assignment",
                target=chain,
            )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
        candidate, receiver = _query_receiver_is_lifecycle_candidate(node)
        if candidate and any(
            keyword.arg == "status"
            for keyword in node.keywords
        ):
            self._record(
                node,
                kind="queryset_update",
                target=f"{receiver}.update(status=...)",
            )
        self.generic_visit(node)


def iter_python_files(shipping_hub_root: Path) -> Iterable[Path]:
    for path in shipping_hub_root.rglob("*.py"):
        relative = path.relative_to(shipping_hub_root)
        if any(part in IGNORED_PARTS for part in relative.parts):
            continue
        normalized = relative.as_posix()
        if normalized in AUTHORIZED_FILES:
            continue
        yield path


def scan_shipping_hub(shipping_hub_root: Path) -> list[DirectStateWrite]:
    findings: list[DirectStateWrite] = []

    for path in iter_python_files(shipping_hub_root):
        relative = path.relative_to(shipping_hub_root).as_posix()
        source = path.read_text(encoding="utf-8-sig")
        try:
            tree = ast.parse(source, filename=relative)
        except SyntaxError as error:
            findings.append(
                DirectStateWrite(
                    file=relative,
                    line=int(error.lineno or 0),
                    column=int(error.offset or 0),
                    kind="syntax_error",
                    target="",
                    code=str(error),
                )
            )
            continue

        visitor = DirectStateWriteVisitor(
            source=source,
            relative_file=relative,
        )
        visitor.visit(tree)
        findings.extend(visitor.findings)

    return sorted(
        findings,
        key=lambda item: (
            item.file,
            item.line,
            item.column,
            item.kind,
        ),
    )


def findings_as_dicts(
    findings: Iterable[DirectStateWrite],
) -> list[dict]:
    return [asdict(item) for item in findings]
