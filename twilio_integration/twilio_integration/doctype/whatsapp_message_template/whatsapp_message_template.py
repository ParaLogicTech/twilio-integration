# Copyright (c) 2021, Frappe and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

class WhatsAppMessageTemplate(Document):

	def get_parameters_dict(self, context):
		"""
		Returns a dictionary of variable:value pairs using the parameters child table.
		Each `value` is rendered using Jinja with the provided context.
		"""
		param_dict = {}
		for param in self.parameters:
			if param.variable and param.value:
				param_dict[param.variable] = frappe.render_template(param.value, context)
		return param_dict

	def get_rendered_body(self, context):
		"""
		Renders the `template_body` field using the context derived from parameters.
		"""
		param_dict = self.get_parameters_dict(context)

		return frappe.render_template(self.template_body, param_dict)
