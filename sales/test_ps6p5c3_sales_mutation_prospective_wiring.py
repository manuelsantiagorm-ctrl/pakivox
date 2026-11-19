import ast
from pathlib import Path

from django.test import SimpleTestCase


ROOT = Path(__file__).resolve().parents[1]
VIEWS = ROOT / "sales" / "views.py"


TARGETS = (
    "agregar_producto_pedido",
    "eliminar_producto_pedido",
    "actualizar_producto_pedido",
)


def _functions():
    source = VIEWS.read_text(
        encoding="utf-8",
        errors="strict",
    )

    tree = ast.parse(
        source,
        filename=str(VIEWS),
    )

    return {
        node.name: node
        for node in tree.body
        if isinstance(
            node,
            (
                ast.FunctionDef,
                ast.AsyncFunctionDef,
            ),
        )
    }


def _direct_calls(node):
    result = set()

    class Collector(ast.NodeVisitor):

        def visit_FunctionDef(self, child):
            if child is node:
                self.generic_visit(child)

        def visit_AsyncFunctionDef(self, child):
            if child is node:
                self.generic_visit(child)

        def visit_Lambda(self, child):
            return

        def visit_Call(self, child):
            func = child.func

            if isinstance(
                func,
                ast.Name,
            ):
                result.add(
                    func.id
                )

            elif isinstance(
                func,
                ast.Attribute,
            ):
                result.add(
                    func.attr
                )

            self.generic_visit(child)

    collector = Collector()

    for statement in node.body:
        collector.visit(
            statement
        )

    return result


class PS6P5C3SalesProspectiveWiringTests(
    SimpleTestCase
):

    def test_mutation_authorization_helper_exists_and_uses_resolver(self):
        functions = _functions()

        self.assertIn(
            "_authorize_order_mutation_activity_route",
            functions,
            msg=(
                "Falta el helper de autorizacion prospectiva de Sales."
            ),
        )

        helper = functions[
            "_authorize_order_mutation_activity_route"
        ]

        calls = _direct_calls(
            helper
        )

        self.assertIn(
            "resolve_order_mutation_authorization_warehouses",
            calls,
        )

        self.assertIn(
            "authorize_activity_route",
            calls,
        )

    def test_add_uses_prospective_authorization(self):
        calls = _direct_calls(
            _functions()[
                "agregar_producto_pedido"
            ]
        )

        self.assertIn(
            "_authorize_order_mutation_activity_route",
            calls,
        )

    def test_delete_uses_prospective_authorization(self):
        calls = _direct_calls(
            _functions()[
                "eliminar_producto_pedido"
            ]
        )

        self.assertIn(
            "_authorize_order_mutation_activity_route",
            calls,
        )

    def test_update_uses_prospective_authorization(self):
        calls = _direct_calls(
            _functions()[
                "actualizar_producto_pedido"
            ]
        )

        self.assertIn(
            "_authorize_order_mutation_activity_route",
            calls,
        )