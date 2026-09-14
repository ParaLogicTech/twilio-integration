# Copyright (c) 2025, Frappe and contributors
# For license information, please see license.txt

from frappe.model.document import Document

from ...utils import validate_not_default_provider


class FreshchatSettings(Document):
	def validate(self):
		validate_not_default_provider(self)
