import frappe
from frappe.model.utils.rename_field import rename_field


def execute():
	if not frappe.db.has_column("WhatsApp Message", "notification_type"):
		return

	rename_field("WhatsApp Message", "notification_type", "notification_key")
	frappe.db.sql_ddl("alter table `tabWhatsApp Message` drop column `notification_type`")
	frappe.cache.hdel("table_columns", "tabWhatsApp Message")
