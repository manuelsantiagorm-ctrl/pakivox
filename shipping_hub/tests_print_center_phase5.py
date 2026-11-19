from django.test import SimpleTestCase

from shipping_hub.models import ShipmentPackage, ShippingPrintItem, ShippingPrintJob
from shipping_hub.views import PRINTABLE_PACKAGE_STATUSES


class PrintCenterPhase5Tests(SimpleTestCase):
    def test_print_job_supports_single_and_batch(self):
        choices = dict(ShippingPrintJob.MODE_CHOICES)
        self.assertIn(ShippingPrintJob.MODE_SINGLE, choices)
        self.assertIn(ShippingPrintJob.MODE_BATCH, choices)

    def test_print_job_has_warehouse_snapshot(self):
        field = ShippingPrintJob._meta.get_field("warehouse_id_snapshot")
        self.assertTrue(field.db_index)

    def test_print_item_protects_package_history(self):
        field = ShippingPrintItem._meta.get_field("package")
        self.assertEqual(field.remote_field.on_delete.__name__, "PROTECT")

    def test_print_item_records_reprint_snapshot(self):
        names = {field.name for field in ShippingPrintItem._meta.fields}
        self.assertTrue({"was_reprint", "previous_print_count", "printed_at"} <= names)

    def test_printable_statuses_exclude_draft_and_failed(self):
        self.assertNotIn(ShipmentPackage.STATUS_DRAFT, PRINTABLE_PACKAGE_STATUSES)
        self.assertNotIn(ShipmentPackage.STATUS_FAILED, PRINTABLE_PACKAGE_STATUSES)
        self.assertIn(ShipmentPackage.STATUS_LABEL_READY, PRINTABLE_PACKAGE_STATUSES)

    def test_print_sequence_must_be_positive(self):
        names = {constraint.name for constraint in ShippingPrintItem._meta.constraints}
        self.assertIn("print_item_sequence_positive", names)

    def test_same_package_cannot_repeat_inside_job(self):
        names = {constraint.name for constraint in ShippingPrintItem._meta.constraints}
        self.assertIn("uniq_print_job_package", names)

    def test_job_statuses_include_completed(self):
        self.assertIn(ShippingPrintJob.STATUS_COMPLETED, dict(ShippingPrintJob.STATUS_CHOICES))
