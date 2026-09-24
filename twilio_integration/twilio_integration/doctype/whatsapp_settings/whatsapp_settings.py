# Copyright (c) 2025, Frappe and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import get_link_to_form
from ...utils import PROVIDER_SETTINGS


class WhatsAppSettings(Document):
	def validate(self):
		self.validate_provider_enabled()

	def validate_provider_enabled(self):
		if not self.whatsapp_provider:
			return

		settings_doctype = PROVIDER_SETTINGS.get(self.whatsapp_provider)
		if not settings_doctype:
			return

		if frappe.db.get_single_value(settings_doctype, "enabled"):
			return

		frappe.throw(
			_("{0} is not enabled. Please enable it in {1} before selecting it as the WhatsApp Provider.").format(
				frappe.bold(self.whatsapp_provider),
				get_link_to_form(settings_doctype, settings_doctype),
			),
			title=_("Provider Not Enabled"),
		)
