import frappe
from frappe.model.utils.rename_field import rename_field


def execute():
	if frappe.db.has_column("WhatsApp Message", "send_on"):
		rename_field("WhatsApp Message", "send_on", "date_sent")

	if frappe.db.has_column("WhatsApp Message", "reference_document_name"):
		rename_field("WhatsApp Message", "reference_document_name", "reference_name")
