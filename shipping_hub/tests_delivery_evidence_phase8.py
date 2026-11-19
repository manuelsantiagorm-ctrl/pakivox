from pathlib import Path
from django.db.models import PROTECT
from django.test import SimpleTestCase
from shipping_hub.forms_delivery import DeliveryEvidenceForm, MultipleImageInput
from shipping_hub.models import DeliveryEvidence, DeliveryEvidenceAsset, DeliveryEvidencePackage


class DeliveryEvidencePhase8Tests(SimpleTestCase):
    def test_results_include_complete_partial_and_rejected(self):
        self.assertEqual({v for v, _ in DeliveryEvidence.RESULT_CHOICES}, {"delivered", "partial", "rejected"})
    def test_shipment_history_is_protected(self):
        self.assertIs(DeliveryEvidence._meta.get_field("carrier_shipment").remote_field.on_delete, PROTECT)
    def test_warehouse_history_is_protected(self):
        self.assertIs(DeliveryEvidence._meta.get_field("warehouse").remote_field.on_delete, PROTECT)
    def test_idempotency_key_is_unique(self):
        self.assertTrue(DeliveryEvidence._meta.get_field("idempotency_key").unique)
    def test_server_controls_recorded_time(self):
        self.assertTrue(DeliveryEvidence._meta.get_field("recorded_at").auto_now_add)
    def test_asset_hash_blocks_duplicates(self):
        self.assertIn("uniq_delivery_evidence_asset_hash", {c.name for c in DeliveryEvidenceAsset._meta.constraints})
    def test_package_cannot_repeat_in_same_evidence(self):
        self.assertIn("uniq_delivery_evidence_package", {c.name for c in DeliveryEvidencePackage._meta.constraints})
    def test_multiple_photo_widget_is_enabled(self):
        self.assertTrue(MultipleImageInput.allow_multiple_selected)
    def test_signature_is_stored_as_protected_file(self):
        self.assertNotIn("signature_data", {f.name for f in DeliveryEvidence._meta.fields})
    def test_location_consent_is_closed_by_default(self):
        self.assertFalse(DeliveryEvidence._meta.get_field("location_consent").default)
    def test_form_has_hidden_idempotency_key(self):
        self.assertEqual(DeliveryEvidenceForm.base_fields["idempotency_key"].widget.input_type, "hidden")
    def test_capture_template_uses_multipart_and_csrf(self):
        text = self._template("delivery_evidence_form.html"); self.assertIn('enctype="multipart/form-data"', text); self.assertIn("{% csrf_token %}", text)
    def test_capture_template_requests_gps_by_explicit_action(self):
        text = self._template("delivery_evidence_form.html"); self.assertIn("get-location", text); self.assertIn("location_consent", text)
    def test_asset_route_replaces_public_media_url(self):
        text = self._template("delivery_evidence_detail.html"); self.assertIn("delivery_evidence_asset", text); self.assertNotIn("asset.file.url", text)
    def test_tracking_detail_links_capture_and_history(self):
        text = self._template("tracking_detail.html"); self.assertIn("delivery_evidence_create", text); self.assertIn("delivery_evidence_detail", text)
    @staticmethod
    def _template(name):
        return (Path(__file__).resolve().parent.parent / "templates" / "shipping_hub" / name).read_text(encoding="utf-8")
