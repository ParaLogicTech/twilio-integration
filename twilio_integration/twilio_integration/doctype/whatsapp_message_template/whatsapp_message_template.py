# Copyright (c) 2021, Frappe and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import cstr
from frappe import _


class WhatsAppMessageTemplate(Document):
	def get_content_variables(self, context):
		"""
		Returns a dictionary of variable:value pairs using the parameters child table.
		Each `value` is rendered using Jinja with the provided context.
		"""
		content_variables = frappe._dict()
		for param in self.parameters:
			if param.variable:
				value = cstr(param.value)
				if "{" in value:
					content_variables[param.variable] = frappe.render_template(value, context)
				else:
					content_variables[param.variable] = cstr(value)

		return content_variables

	def get_rendered_body(self, context, content_variables=None):
		"""
		Renders the `template_body` field using the context derived from parameters.
		"""
		if content_variables is None:
			content_variables = self.get_content_variables(context)

		return frappe.render_template(self.template_body, content_variables)

@frappe.whitelist()
def sync_twilio_template(template_sid, template_name):
	from ...twilio_handler import Twilio

	twilio = Twilio.connect()
	content = twilio.get_whatsapp_template(template_sid)

	if not content:
		frappe.throw(_("Unable to fetch template from Twilio"))

	doc = frappe.get_cached_doc("WhatsApp Message Template", template_name)
	doc.db_set("template_body", content.types.get("twilio/text", {}).get("body", ""))

	return _("Template synced successfully from Twilio")
