import ast
from pathlib import Path

from django.test import SimpleTestCase

from accounts.simple_activity_route_contract import (
    ROUTE_ACTION_CONTRACTS,
)


ROOT = Path(__file__).resolve().parents[1]
SALES_VIEWS = ROOT / "sales" / "views.py"


TARGET_ROUTES = (
    "agregar_producto_pedido",
    "eliminar_producto_pedido",
    "actualizar_producto_pedido",
)


CONTROL_ROUTES = (
    "actualizar_estado_pedido",
    "actualizar_pago_pedido",
    "marcar_pedido_pagado",
    "cancelar_pedido",
    "recuperar_pedido_error_humano",
    "cancelar_pedido_comercialmente",
)


class _DirectCallCollector(ast.NodeVisitor):
    """
    Recolecta llamadas del cuerpo real de una funcion.

    No desciende a funciones internas para evitar falsos positivos.
    """

    def __init__(self):
        self.calls = set()

    def visit_FunctionDef(self, node):
        return

    def visit_AsyncFunctionDef(self, node):
        return

    def visit_Lambda(self, node):
        return

    def visit_Call(self, node):
        func = node.func

        if isinstance(func, ast.Name):
            self.calls.add(func.id)

        elif isinstance(func, ast.Attribute):
            self.calls.add(func.attr)

        self.generic_visit(node)


def _sales_functions():
    source = SALES_VIEWS.read_text(
        encoding="utf-8",
        errors="strict",
    )

    tree = ast.parse(
        source,
        filename=str(SALES_VIEWS),
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


def _direct_calls(function_node):
    collector = _DirectCallCollector()

    for statement in function_node.body:
        collector.visit(statement)

    return collector.calls


def _route_contracts():
    return {
        item.key: item
        for item in ROUTE_ACTION_CONTRACTS
    }


class PS6P5BSalesMutationAuthorityRedTests(SimpleTestCase):

    def test_contract_requires_operate_multiwarehouse_for_all_three(self):
        contracts = _route_contracts()

        for route_name in TARGET_ROUTES:
            with self.subTest(route=route_name):
                contract = contracts[
                    f"sales:{route_name}"
                ]

                self.assertEqual(
                    contract.activity_code,
                    "sales_customers",
                )

                self.assertEqual(
                    contract.minimum_level,
                    "operate",
                )

                self.assertEqual(
                    contract.capability_code,
                    "sales.create",
                )

                self.assertEqual(
                    contract.scope,
                    "multiwarehouse",
                )

    def test_existing_sensitive_sales_controls_use_authority(self):
        functions = _sales_functions()

        for route_name in CONTROL_ROUTES:
            with self.subTest(route=route_name):
                function_node = functions[
                    route_name
                ]

                self.assertIn(
                    "_authorize_order_activity_route",
                    _direct_calls(function_node),
                    msg=(
                        f"{route_name} dejo de usar "
                        "_authorize_order_activity_route."
                    ),
                )

    def test_add_item_requires_order_activity_authority(self):
        functions = _sales_functions()

        self.assertIn(
            "_authorize_order_activity_route",
            _direct_calls(
                functions[
                    "agregar_producto_pedido"
                ]
            ),
            msg=(
                "agregar_producto_pedido cumple company scope "
                "pero no aplica autoridad OPERATE multibodega."
            ),
        )

    def test_delete_item_requires_order_activity_authority(self):
        functions = _sales_functions()

        self.assertIn(
            "_authorize_order_activity_route",
            _direct_calls(
                functions[
                    "eliminar_producto_pedido"
                ]
            ),
            msg=(
                "eliminar_producto_pedido cumple company scope "
                "pero no aplica autoridad OPERATE multibodega."
            ),
        )

    def test_update_item_requires_order_activity_authority(self):
        functions = _sales_functions()

        self.assertIn(
            "_authorize_order_activity_route",
            _direct_calls(
                functions[
                    "actualizar_producto_pedido"
                ]
            ),
            msg=(
                "actualizar_producto_pedido cumple company scope "
                "pero no aplica autoridad OPERATE multibodega."
            ),
        )