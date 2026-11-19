from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.urls import reverse

from accounts.models import (
    Capability,
    EmployeeCapabilityAssignment,
    UserProfile,
    UserWarehouseAccess,
)
from companies.models import Company
from inventory.models import Warehouse

from notifications.access import notifications_for_user
from notifications.context_processors import notification_context
from notifications.models import (
    Notification,
    NotificationUserState,
)


User = get_user_model()


class PS6P4BNotificationAudienceTests(TestCase):

    def setUp(self):
        self.company = Company.objects.create(
            name="PS6P4B Empresa",
            slug="ps6p4b-empresa",
        )

        self.foreign_company = Company.objects.create(
            name="PS6P4B Ajena",
            slug="ps6p4b-ajena",
        )

        self.user = User.objects.create_user(
            username="ps6p4b@test.local",
            password="x",
        )

        UserProfile.objects.create(
            user=self.user,
            company=self.company,
            role="sales",
            is_active=True,
            employment_status=(
                UserProfile.STATUS_ACTIVE
            ),
        )

        self.warehouse = Warehouse.objects.create(
            company=self.company,
            name="PS6P4B A",
            code="PS6P4B-A",
        )

        self.other_warehouse = Warehouse.objects.create(
            company=self.company,
            name="PS6P4B B",
            code="PS6P4B-B",
        )

        self.foreign_warehouse = Warehouse.objects.create(
            company=self.foreign_company,
            name="PS6P4B X",
            code="PS6P4B-X",
        )

        self.factory = RequestFactory()

    def grant(self, code):
        capability = Capability.objects.get(
            code=code
        )

        assignment, _created = (
            EmployeeCapabilityAssignment.objects
            .update_or_create(
                company=self.company,
                user=self.user,
                capability=capability,
                defaults={
                    "source": (
                        EmployeeCapabilityAssignment
                        .SOURCE_DIRECT
                    ),
                    "is_active": True,
                },
            )
        )

        return assignment

    def revoke(self, code):
        EmployeeCapabilityAssignment.objects.filter(
            company=self.company,
            user=self.user,
            capability__code=code,
        ).update(
            is_active=False,
        )

    def set_level(
        self,
        level,
        *,
        warehouse=None,
    ):
        warehouse = warehouse or self.warehouse

        access, _created = (
            UserWarehouseAccess.objects
            .update_or_create(
                company=self.company,
                user=self.user,
                warehouse=warehouse,
                defaults={
                    "access_level": level,
                    "is_active": True,
                },
            )
        )

        return access

    def notification(
        self,
        event_type,
        *,
        company=None,
        warehouse=None,
        suffix="",
    ):
        company = company or self.company

        if warehouse is None:
            warehouse = self.warehouse

        return Notification.objects.create(
            company=company,
            warehouse=warehouse,
            category="ps6p4b",
            event_type=event_type,
            level=Notification.LEVEL_WARNING,
            status=Notification.STATUS_ACTIVE,
            title=f"{event_type} {suffix}",
            message="PS6P4B",
            action_label="Ver",
            action_name="notifications:center",
            action_kwargs={},
            source_app="ps6p4b",
            source_model="Test",
            source_object_id=1,
            dedupe_key=(
                f"ps6p4b:{event_type}:"
                f"{warehouse.pk if warehouse else 0}:"
                f"{suffix}"
            ),
            reference=suffix,
        )

    def visible_ids(self):
        return set(
            notifications_for_user(
                self.user
            ).values_list(
                "id",
                flat=True,
            )
        )

    def test_stock_event_requires_purchasing_capability(self):
        self.set_level(
            UserWarehouseAccess.LEVEL_OPERATE
        )

        alert = self.notification(
            "low_stock",
            suffix="no-cap",
        )

        self.assertNotIn(
            alert.id,
            self.visible_ids(),
        )

    def test_stock_event_requires_operate_level(self):
        self.grant(
            "purchases.create"
        )

        self.set_level(
            UserWarehouseAccess.LEVEL_VIEW
        )

        alert = self.notification(
            "low_stock",
            suffix="view-only",
        )

        self.assertNotIn(
            alert.id,
            self.visible_ids(),
        )

    def test_stock_event_does_not_require_cost_permission(self):
        self.grant(
            "purchases.create"
        )

        self.set_level(
            UserWarehouseAccess.LEVEL_OPERATE
        )

        low = self.notification(
            "low_stock",
            suffix="low",
        )

        out = self.notification(
            "out_of_stock",
            suffix="out",
        )

        visible = self.visible_ids()

        self.assertIn(
            low.id,
            visible,
        )

        self.assertIn(
            out.id,
            visible,
        )

        self.assertFalse(
            EmployeeCapabilityAssignment.objects.filter(
                company=self.company,
                user=self.user,
                capability__code="purchases.costs.view",
                is_active=True,
            ).exists()
        )

    def test_purchase_overdue_requires_purchase_view(self):
        self.grant(
            "purchases.view"
        )

        self.set_level(
            UserWarehouseAccess.LEVEL_VIEW
        )

        item = self.notification(
            "purchase_order_overdue",
            suffix="po",
        )

        self.assertIn(
            item.id,
            self.visible_ids(),
        )
    def test_inventory_management_events_require_manage(self):
        self.grant(
            "inventory.manage"
        )

        self.set_level(
            UserWarehouseAccess.LEVEL_OPERATE
        )

        staging = self.notification(
            "staging_overdue",
            suffix="staging",
        )

        self.assertNotIn(
            staging.id,
            self.visible_ids(),
        )

        self.set_level(
            UserWarehouseAccess.LEVEL_MANAGE
        )

        visible = self.visible_ids()

        self.assertIn(
            staging.id,
            visible,
        )

    def test_same_company_unauthorized_warehouse_is_hidden(self):
        self.grant(
            "purchases.create"
        )

        self.set_level(
            UserWarehouseAccess.LEVEL_OPERATE
        )

        item = self.notification(
            "low_stock",
            warehouse=self.other_warehouse,
            suffix="other-wh",
        )

        self.assertNotIn(
            item.id,
            self.visible_ids(),
        )

    def test_foreign_company_is_hidden(self):
        self.grant(
            "purchases.create"
        )

        self.set_level(
            UserWarehouseAccess.LEVEL_OPERATE
        )

        item = self.notification(
            "low_stock",
            company=self.foreign_company,
            warehouse=self.foreign_warehouse,
            suffix="foreign",
        )

        self.assertNotIn(
            item.id,
            self.visible_ids(),
        )

    def test_unknown_event_fails_closed(self):
        self.grant(
            "purchases.create"
        )

        self.set_level(
            UserWarehouseAccess.LEVEL_MANAGE
        )

        item = self.notification(
            "unknown_ps6p4b_event",
            suffix="unknown",
        )

        self.assertNotIn(
            item.id,
            self.visible_ids(),
        )

    def test_owner_only_sees_known_events_inside_own_company(self):
        owner = User.objects.create_user(
            username="ps6p4b-owner@test.local",
            password="x",
        )

        UserProfile.objects.create(
            user=owner,
            company=self.company,
            role="owner",
            is_active=True,
            employment_status=(
                UserProfile.STATUS_ACTIVE
            ),
        )

        known = self.notification(
            "low_stock",
            suffix="owner-known",
        )

        unknown = self.notification(
            "unknown_ps6p4b_owner",
            suffix="owner-unknown",
        )

        foreign = self.notification(
            "low_stock",
            company=self.foreign_company,
            warehouse=self.foreign_warehouse,
            suffix="owner-foreign",
        )

        visible = set(
            notifications_for_user(
                owner
            ).values_list(
                "id",
                flat=True,
            )
        )

        self.assertIn(
            known.id,
            visible,
        )

        self.assertNotIn(
            unknown.id,
            visible,
        )

        self.assertNotIn(
            foreign.id,
            visible,
        )

    def test_revoked_capability_hides_existing_notification(self):
        self.grant(
            "purchases.create"
        )

        self.set_level(
            UserWarehouseAccess.LEVEL_OPERATE
        )

        item = self.notification(
            "low_stock",
            suffix="revoke",
        )

        self.assertIn(
            item.id,
            self.visible_ids(),
        )

        self.revoke(
            "purchases.create"
        )

        self.assertNotIn(
            item.id,
            self.visible_ids(),
        )

    def test_open_direct_id_of_ineligible_notification_is_404(self):
        self.grant(
            "inventory.view"
        )

        self.set_level(
            UserWarehouseAccess.LEVEL_OPERATE
        )

        item = self.notification(
            "low_stock",
            suffix="direct-open",
        )

        self.client.force_login(
            self.user
        )

        response = self.client.get(
            reverse(
                "notifications:open",
                kwargs={
                    "notification_id": item.id,
                },
            )
        )

        self.assertEqual(
            response.status_code,
            404,
        )

        self.assertFalse(
            NotificationUserState.objects.filter(
                notification=item,
                user=self.user,
            ).exists()
        )

    def test_mark_read_direct_id_of_ineligible_notification_is_404(self):
        self.grant(
            "inventory.view"
        )

        self.set_level(
            UserWarehouseAccess.LEVEL_OPERATE
        )

        item = self.notification(
            "low_stock",
            suffix="direct-read",
        )

        self.client.force_login(
            self.user
        )

        response = self.client.post(
            reverse(
                "notifications:mark_read",
                kwargs={
                    "notification_id": item.id,
                },
            )
        )

        self.assertEqual(
            response.status_code,
            404,
        )

    def test_mark_all_read_only_touches_eligible_notifications(self):
        self.grant(
            "purchases.view"
        )

        self.set_level(
            UserWarehouseAccess.LEVEL_VIEW
        )

        eligible = self.notification(
            "purchase_order_overdue",
            suffix="eligible",
        )

        blocked = self.notification(
            "staging_overdue",
            suffix="blocked",
        )

        self.client.force_login(
            self.user
        )

        response = self.client.post(
            reverse(
                "notifications:mark_all_read"
            )
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        self.assertTrue(
            NotificationUserState.objects.filter(
                notification=eligible,
                user=self.user,
                is_read=True,
            ).exists()
        )

        self.assertFalse(
            NotificationUserState.objects.filter(
                notification=blocked,
                user=self.user,
            ).exists()
        )

    def test_context_processor_counts_only_eligible_events(self):
        self.grant(
            "purchases.view"
        )

        self.set_level(
            UserWarehouseAccess.LEVEL_VIEW
        )

        eligible = self.notification(
            "purchase_order_overdue",
            suffix="ctx-visible",
        )

        blocked = self.notification(
            "low_stock",
            suffix="ctx-hidden",
        )

        request = self.factory.get(
            "/accounts/employee/home/"
        )

        request.user = self.user

        context = notification_context(
            request
        )

        ids = {
            item.id
            for item in context[
                "notification_dropdown_items"
            ]
        }

        self.assertEqual(
            context[
                "notification_unread_count"
            ],
            1,
        )

        self.assertIn(
            eligible.id,
            ids,
        )

        self.assertNotIn(
            blocked.id,
            ids,
        )
