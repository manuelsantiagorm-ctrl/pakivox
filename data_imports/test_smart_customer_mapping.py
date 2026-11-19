from django.test import SimpleTestCase

from data_imports.forms import CustomerMappingForm
from data_imports.services.customer_import import (
    apply_customer_mapping_defaults,
    build_customer_mapping_analysis,
    sanitize_customer_mapping_defaults,
    suggest_mapping,
)


class SmartCustomerMappingTests(SimpleTestCase):
    def test_exact_official_full_name_header_is_auto_mapped(self):
        mapping = suggest_mapping(["Nombre completo / Razón social"])
        self.assertEqual(mapping.get("0"), "customer.full_name")

    def test_large_file_headers_keep_only_recognized_columns(self):
        headers = [
            "Columna irrelevante " + str(index)
            for index in range(90)
        ] + [
            "Nombre cliente",
            "RFC",
            "E-mail",
            "Celular",
            "WhatsApp",
            "Calle",
            "Número exterior",
            "Colonia",
            "Ciudad",
            "CP",
        ]

        mapping = suggest_mapping(headers)

        mapped = {
            target
            for target in mapping.values()
            if target
        }

        self.assertIn("customer.full_name", mapped)
        self.assertIn("customer.rfc", mapped)
        self.assertIn("customer.email", mapped)
        self.assertIn("customer.phone", mapped)
        self.assertIn("customer.whatsapp", mapped)
        self.assertIn("address.street", mapped)
        self.assertIn("address.exterior_number", mapped)
        self.assertIn("address.neighborhood", mapped)
        self.assertIn("address.city", mapped)
        self.assertIn("address.zip_code", mapped)

        analysis = build_customer_mapping_analysis(
            headers=headers,
            mapping=mapping,
        )

        self.assertEqual(analysis["total_columns"], 100)
        self.assertEqual(analysis["mapped_columns"], 10)
        self.assertEqual(analysis["ignored_columns"], 90)

    def test_required_full_name_cannot_be_ignored(self):
        form = CustomerMappingForm(
            data={
                "column_0": "customer.email",
            },
            headers=["Correo"],
        )

        self.assertFalse(form.is_valid())
        self.assertIn(
            "Nombre completo / Razón social",
            str(form.non_field_errors()),
        )

    def test_optional_country_can_receive_global_value(self):
        mapping = {
            "0": "customer.full_name",
        }
        defaults = sanitize_customer_mapping_defaults(
            values={
                "address.country": "México",
            },
            mapping=mapping,
        )

        self.assertEqual(
            defaults,
            {"address.country": "México"},
        )

        data = apply_customer_mapping_defaults(
            {"customer.full_name": "Cliente Uno"},
            defaults,
        )

        self.assertEqual(data["address.country"], "México")

    def test_defaults_do_not_overwrite_real_row_values(self):
        data = apply_customer_mapping_defaults(
            {
                "customer.full_name": "Cliente Uno",
                "address.country": "Colombia",
            },
            {
                "address.country": "México",
            },
        )

        self.assertEqual(data["address.country"], "Colombia")

    def test_required_field_is_never_accepted_as_global_default(self):
        defaults = sanitize_customer_mapping_defaults(
            values={
                "customer.full_name": "Mismo nombre para todos",
                "address.country": "México",
            },
            mapping={},
        )

        self.assertNotIn("customer.full_name", defaults)
        self.assertEqual(defaults["address.country"], "México")

    def test_analysis_marks_country_as_optional_missing(self):
        analysis = build_customer_mapping_analysis(
            headers=["Nombre"],
            mapping={"0": "customer.full_name"},
            defaults={},
        )

        country = next(
            item
            for item in analysis["missing_fields"]
            if item["target"] == "address.country"
        )

        self.assertEqual(country["level"], "optional")
        self.assertTrue(country["can_ignore"])
        self.assertTrue(analysis["ready_for_validation"])
