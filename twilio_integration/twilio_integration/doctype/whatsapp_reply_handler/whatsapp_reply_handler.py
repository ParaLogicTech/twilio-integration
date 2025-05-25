# Copyright (c) 2025, Frappe and contributors
# For license information, please see license.txt

import frappe
# from frappe import _
from frappe.utils import cstr
from frappe.model.document import Document
from frappe.utils.jinja import validate_template
from frappe.utils.safe_exec import safe_exec, get_safe_globals


class WhatsAppReplyHandler(Document):
	def validate(self):
		self.validate_actions()

	def validate_actions(self):
		for d in self.actions:
			validate_template(cstr(d.reply_message))

	def handle_incoming_message(self, incoming_message, context_message):
		eval_globals = get_safe_globals()
		context = frappe._dict({
			"message": cstr(incoming_message.message),
			"incoming_message_doc": incoming_message,
			"context_message_doc": context_message,
			"doc": frappe._dict(),
			"reference_doctype": incoming_message.reference_doctype,
			"reference_name": incoming_message.reference_name,
			"incoming_media_status": incoming_message.incoming_media_status,
			"reply_message": None,
		})

		if (
			incoming_message.reference_doctype
			and incoming_message.reference_name
			and frappe.db.exists(incoming_message.reference_doctype, incoming_message.reference_name)
		):
			context["doc"] = frappe.get_doc(incoming_message.reference_doctype, incoming_message.reference_name)

		reply_message = None

		for d in self.actions:
			if not cstr(d.condition).strip() or frappe.safe_eval(d.condition, eval_globals, context):
				reply_message = self.handle_reply_action(d, context)
				break

		return reply_message

	def handle_reply_action(self, row, context):
		if cstr(row.action).strip():
			safe_exec(row.action, _locals=context, script_filename=f"WhatsApp Reply Handler {self.name}")

		reply_message = context.get("reply_message")

		if not reply_message and cstr(row.reply_message).strip():
			if "{" in row.reply_message:
				reply_message = frappe.render_template(row.reply_message, context)
			else:
				reply_message = row.reply_message

		if row.expire_reply_handler and context.context_message_doc:
			context.context_message_doc.db_set("reply_handler_expired", 1)

		return reply_message
